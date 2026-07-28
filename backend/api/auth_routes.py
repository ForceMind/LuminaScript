import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import models
import schemas
from database import get_db
from services.audit import log_login
from services.login_limiter import clear_failures, get_retry_after, record_failure


logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/token", response_model=schemas.Token)
async def login_for_access_token(
    request: Request,
    background_tasks: BackgroundTasks,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    logger.info("收到登录请求: 用户名=%s", form_data.username)

    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        ip = forwarded.split(",")[-1].strip()
    else:
        ip = request.client.host if request.client else "unknown"

    login_key = f"{ip}|{form_data.username.strip().casefold()}"
    retry_after = await get_retry_after(login_key)
    if retry_after:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(retry_after)},
        )

    result = await db.execute(
        select(models.User).where(models.User.username == form_data.username)
    )
    user = result.scalars().first()
    user_agent = request.headers.get("user-agent", "")

    password_hash = user.hashed_password if user else auth.DUMMY_PASSWORD_HASH
    password_valid = auth.verify_password(form_data.password, password_hash)

    if not user:
        logger.warning("登录失败: 用户 %s 不存在", form_data.username)
    elif not password_valid:
        logger.warning("登录失败: 用户 %s 密码错误", form_data.username)
        background_tasks.add_task(
            log_login,
            user_id=user.id,
            ip=ip,
            status="failed",
            user_agent_str=user_agent,
        )

    if not user or not password_valid:
        await record_failure(login_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if user.is_admin and form_data.password == "admin123":
        await record_failure(login_key)
        logger.critical(
            "Blocked login for administrator %s because it still uses the "
            "disabled default password.",
            user.username,
        )
        raise HTTPException(
            status_code=403,
            detail=(
                "默认管理员密码已被禁用。请在服务器运行 "
                "`python backend/manage_admin.py` 设置新密码。"
            ),
        )

    await clear_failures(login_key)
    background_tasks.add_task(
        log_login,
        user_id=user.id,
        ip=ip,
        status="success",
        user_agent_str=user_agent,
    )

    access_token = auth.create_access_token(
        data={
            "sub": user.username,
            "pwd": auth.password_token_version(user.hashed_password),
        }
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/auth/register", response_model=schemas.UserResponse)
async def register(
    user: schemas.UserCreate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(models.User).where(models.User.username == user.username)
    )
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Username already registered")

    new_user = models.User(
        username=user.username,
        hashed_password=auth.get_password_hash(user.password),
    )
    db.add(new_user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Username already registered",
        )
    await db.refresh(new_user)
    return new_user


@router.get("/users/me", response_model=schemas.UserResponse)
async def read_users_me(
    current_user: models.User = Depends(auth.get_current_user),
):
    return current_user


@router.post("/users/me/password")
async def change_password(
    payload: schemas.PasswordChange,
    db: AsyncSession = Depends(get_db),
    current_user: models.User = Depends(auth.get_current_user),
):
    if not auth.verify_password(
        payload.current_password,
        current_user.hashed_password,
    ):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    if auth.verify_password(payload.new_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="新密码不能与当前密码相同")

    current_user.hashed_password = auth.get_password_hash(payload.new_password)
    await db.commit()
    return {"status": "password_updated"}
