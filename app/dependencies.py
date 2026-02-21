from fastapi import Cookie, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
import uuid

from app.database import get_db
from app.models.models import User


async def get_current_user(
    morphy_user_id: Optional[str] = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Dependency que identifica al usuario autenticado via cookie.
    Se setea automáticamente al completar el OAuth de Strava.
    """
    if not morphy_user_id:
        raise HTTPException(
            status_code=401,
            detail="No autenticado. Ve a /auth/strava/connect primero."
        )

    try:
        user_uuid = uuid.UUID(morphy_user_id)
    except ValueError:
        raise HTTPException(status_code=401, detail="Sesión inválida.")

    result = await db.execute(select(User).where(User.id == user_uuid))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Usuario no encontrado. Reconecta tu cuenta de Strava."
        )

    return user