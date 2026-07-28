from fastapi import Depends, HTTPException

import auth
import models


async def require_admin(
    current_user: models.User = Depends(auth.get_current_user),
) -> models.User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user
