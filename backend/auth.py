from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4
from jwt import InvalidTokenError
import jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from models import User
from database import get_db
from core.config import settings
import hashlib
import hmac

# --- Config ---
SECRET_KEY = settings.require_secure_secret_key()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
DUMMY_PASSWORD_HASH = pwd_context.hash(uuid4().hex)

# --- Utils ---
def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)


def password_token_version(hashed_password: str) -> str:
    """Create a non-reversible marker that revokes JWTs after password changes."""
    return hashlib.sha256(str(hashed_password or "").encode("utf-8")).hexdigest()[:24]


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now, "jti": uuid4().hex})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- Dependency ---
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        password_version: str = payload.get("pwd")
        if username is None or not password_version:
            raise credentials_exception
    except InvalidTokenError:
        raise credentials_exception
    
    # Query User
    # Note: select(User).where... import select first
    from sqlalchemy import select
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    
    if user is None:
        raise credentials_exception
    expected_password_version = password_token_version(user.hashed_password)
    if not hmac.compare_digest(password_version, expected_password_version):
        raise credentials_exception
    return user
