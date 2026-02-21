from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.models import User

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    max_hr: Optional[int] = None
    rest_hr: Optional[int] = None
    age: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    sex: Optional[str] = None  # "M" o "F"
    primary_sport: Optional[str] = None
    experience_level: Optional[str] = None  # "beginner", "intermediate", "advanced", "elite"


@router.get("/")
async def get_profile(db: AsyncSession = Depends(get_db)):
    """Retorna el perfil del atleta con zonas de entrenamiento calculadas."""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="No hay usuario. Ve a /auth/strava/connect primero.")

    # Calcular zonas si tiene max_hr y rest_hr
    zones = None
    if user.max_hr and user.rest_hr:
        hrr = user.max_hr - user.rest_hr  # Heart Rate Reserve
        zones = {
            "Z1_recovery":    f"{round(user.rest_hr + hrr * 0.50)}–{round(user.rest_hr + hrr * 0.60)} bpm",
            "Z2_aerobic":     f"{round(user.rest_hr + hrr * 0.60)}–{round(user.rest_hr + hrr * 0.70)} bpm",
            "Z3_tempo":       f"{round(user.rest_hr + hrr * 0.70)}–{round(user.rest_hr + hrr * 0.80)} bpm",
            "Z4_threshold":   f"{round(user.rest_hr + hrr * 0.80)}–{round(user.rest_hr + hrr * 0.90)} bpm",
            "Z5_vo2max":      f"{round(user.rest_hr + hrr * 0.90)}–{user.max_hr} bpm",
        }

    # TRIMP quality: cuánto mejora con HR real
    trimp_quality = "alta" if (user.max_hr and user.rest_hr) else "baja (sin max_hr/rest_hr)"

    return {
        "name": user.name,
        "email": user.email,
        "age": user.age,
        "weight_kg": user.weight_kg,
        "height_cm": user.height_cm,
        "sex": user.sex,
        "max_hr": user.max_hr,
        "rest_hr": user.rest_hr,
        "primary_sport": user.primary_sport,
        "experience_level": user.experience_level,
        "training_zones": zones,
        "trimp_precision": trimp_quality,
        "tip": None if (user.max_hr and user.rest_hr) else "Agrega max_hr y rest_hr con PUT /profile/ para mejorar la precisión del motor"
    }


@router.put("/")
async def update_profile(data: ProfileUpdate, db: AsyncSession = Depends(get_db)):
    """Actualiza los datos del perfil del atleta."""
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="No hay usuario. Ve a /auth/strava/connect primero.")

    # Actualizar solo los campos enviados
    updated_fields = []
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(user, field, value)
        updated_fields.append(field)

    if not updated_fields:
        return {"message": "No se enviaron campos para actualizar"}

    await db.commit()

    return {
        "status": "updated",
        "updated_fields": updated_fields,
        "message": "Perfil actualizado. El motor de decisión usará estos datos en el próximo cálculo."
    }