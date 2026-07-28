import os

from passlib.context import CryptContext
from sqlalchemy import select

import models
from database import SessionLocal


password_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AdminProvisioningRequired(RuntimeError):
    """Raised when an operator must establish safe administrator credentials."""


def validate_admin_credentials(username: str, password: str) -> tuple[str, str]:
    normalized_username = str(username or "").strip()
    if (
        len(normalized_username) < 3
        or any(
            character.isspace() or ord(character) < 32
            for character in normalized_username
        )
    ):
        raise ValueError(
            "ADMIN_USER must contain at least 3 non-whitespace characters."
        )
    if len(password) < 10 or len(password.encode("utf-8")) > 72:
        raise ValueError(
            "ADMIN_PASS must be 10-72 UTF-8 bytes and at least 10 characters."
        )
    return normalized_username, password


async def ensure_admin_policy() -> list[str]:
    update_admin = os.environ.get("UPDATE_ADMIN", "false").lower() == "true"
    admin_user = os.environ.get("ADMIN_USER", "admin")
    admin_pass = os.environ.get("ADMIN_PASS", "")

    async with SessionLocal() as db:
        admins = (
            await db.execute(
                select(models.User)
                .where(models.User.is_admin == 1)
                .order_by(models.User.id.asc())
            )
        ).scalars().all()

        if not update_admin:
            if not admins:
                raise AdminProvisioningRequired(
                    "No administrator exists. Run `python manage_admin.py` "
                    "before starting the API."
                )
            for administrator in admins:
                try:
                    uses_default = (
                        administrator.hashed_password
                        and password_context.verify(
                            "admin123",
                            administrator.hashed_password,
                        )
                    )
                except Exception:
                    uses_default = False
                if uses_default:
                    raise AdminProvisioningRequired(
                        "An administrator still uses the disabled default "
                        "password. Run `python manage_admin.py` before starting "
                        "the API."
                    )
            return [administrator.username for administrator in admins]

        admin_user, admin_pass = validate_admin_credentials(
            admin_user,
            admin_pass,
        )
        target = (
            await db.execute(
                select(models.User).where(
                    models.User.username == admin_user
                )
            )
        ).scalars().first()

        hashed_password = password_context.hash(admin_pass)
        if target:
            target.is_admin = 1
            target.hashed_password = hashed_password
        else:
            target = models.User(
                username=admin_user,
                hashed_password=hashed_password,
                is_admin=1,
            )
            db.add(target)

        await db.commit()
        result = await db.execute(
            select(models.User.username)
            .where(models.User.is_admin == 1)
            .order_by(models.User.id.asc())
        )
        return list(result.scalars().all())
