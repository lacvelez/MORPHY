from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.models import User
from app.dependencies import get_current_user

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    max_hr: Optional[int] = None
    rest_hr: Optional[int] = None
    age: Optional[int] = None
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    sex: Optional[str] = None
    primary_sport: Optional[str] = None
    experience_level: Optional[str] = None


@router.get("/")
async def get_profile(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    zones = None
    if current_user.max_hr and current_user.rest_hr:
        hrr = current_user.max_hr - current_user.rest_hr
        zones = {
            "Z1_recovery":  f"{round(current_user.rest_hr + hrr * 0.50)}–{round(current_user.rest_hr + hrr * 0.60)} bpm",
            "Z2_aerobic":   f"{round(current_user.rest_hr + hrr * 0.60)}–{round(current_user.rest_hr + hrr * 0.70)} bpm",
            "Z3_tempo":     f"{round(current_user.rest_hr + hrr * 0.70)}–{round(current_user.rest_hr + hrr * 0.80)} bpm",
            "Z4_threshold": f"{round(current_user.rest_hr + hrr * 0.80)}–{round(current_user.rest_hr + hrr * 0.90)} bpm",
            "Z5_vo2max":    f"{round(current_user.rest_hr + hrr * 0.90)}–{current_user.max_hr} bpm",
        }

    trimp_quality = "alta" if (current_user.max_hr and current_user.rest_hr) else "baja (sin max_hr/rest_hr)"

    return {
        "name": current_user.name,
        "email": current_user.email,
        "age": current_user.age,
        "weight_kg": current_user.weight_kg,
        "height_cm": current_user.height_cm,
        "sex": current_user.sex,
        "max_hr": current_user.max_hr,
        "rest_hr": current_user.rest_hr,
        "primary_sport": current_user.primary_sport,
        "experience_level": current_user.experience_level,
        "training_zones": zones,
        "trimp_precision": trimp_quality,
        "tip": None if (current_user.max_hr and current_user.rest_hr)
               else "Agrega max_hr y rest_hr con PUT /profile/ para mejorar la precisión del motor"
    }


@router.put("/")
async def update_profile(
    data: ProfileUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    updated_fields = []
    for field, value in data.model_dump(exclude_none=True).items():
        setattr(current_user, field, value)
        updated_fields.append(field)

    if not updated_fields:
        return {"message": "No se enviaron campos para actualizar"}

    await db.commit()
    return {
        "status": "updated",
        "updated_fields": updated_fields,
        "message": "Perfil actualizado. El motor usará estos datos en el próximo cálculo."
    }