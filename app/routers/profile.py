from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.models import User

router = APIRouter(prefix="/profile", tags=["profile"])

class ProfileUpdate(BaseModel):
    age: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    sex: Optional[str] = None
    max_hr: Optional[int] = None
    rest_hr: Optional[int] = None
    primary_sport: Optional[str] = None
    experience_level: Optional[str] = None

@router.get("/")
async def get_profile(db: AsyncSession = Depends(get_db)):
    """Obtiene el perfil del atleta"""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No user found")

    # Estimar max_hr si no está definido
    estimated_max_hr = None
    if not user.max_hr and user.age:
        estimated_max_hr = 220 - user.age

    return {
        "name": user.name,
        "age": user.age,
        "weight_kg": user.weight_kg,
        "height_cm": user.height_cm,
        "sex": user.sex,
        "max_hr": user.max_hr,
        "estimated_max_hr": estimated_max_hr,
        "rest_hr": user.rest_hr,
        "primary_sport": user.primary_sport,
        "experience_level": user.experience_level,
        "profile_complete": all([user.age, user.weight_kg, user.sex, user.rest_hr])
    }

@router.put("/")
async def update_profile(profile: ProfileUpdate, db: AsyncSession = Depends(get_db)):
    """Actualiza el perfil del atleta"""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No user found")

    # Actualizar solo campos enviados
    update_data = profile.model_dump(exclude_none=True)
    for field, value in update_data.items():
        setattr(user, field, value)

    # Auto-calcular max_hr si no está definido pero tenemos edad
    if not user.max_hr and user.age:
        user.max_hr = 220 - user.age

    await db.commit()

    return {
        "status": "updated",
        "profile": {
            "name": user.name,
            "age": user.age,
            "weight_kg": user.weight_kg,
            "height_cm": user.height_cm,
            "sex": user.sex,
            "max_hr": user.max_hr,
            "rest_hr": user.rest_hr,
            "primary_sport": user.primary_sport,
            "experience_level": user.experience_level
        }
    }