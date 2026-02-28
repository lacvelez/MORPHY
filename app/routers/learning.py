"""
Sprint 11: Router del motor de aprendizaje adaptativo.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.models import User, AthleteThresholdAdjustment
from app.dependencies import get_current_user
from app.services.learning_engine  import (
    analyze_and_update,
    get_learning_status,
    get_or_create_thresholds,
)

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/status")
async def learning_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await get_learning_status(current_user.id, db)


@router.post("/trigger")
async def trigger_learning(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Dispara el análisis de feedback. Llamar después de guardar cada feedback."""
    return await analyze_and_update(current_user.id, db)


class LearningConfig(BaseModel):
    learning_speed: str           # conservative | moderate | fast | custom
    custom_min_signals: Optional[int] = None


@router.put("/config")
async def update_config(
    config: LearningConfig,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    valid = {"conservative", "moderate", "fast", "custom"}
    if config.learning_speed not in valid:
        raise HTTPException(400, f"learning_speed debe ser: {valid}")

    if config.learning_speed == "custom":
        if not config.custom_min_signals or not (1 <= config.custom_min_signals <= 50):
            raise HTTPException(400, "custom_min_signals debe estar entre 1 y 50")

    adj = await get_or_create_thresholds(current_user.id, db)
    adj.learning_speed = config.learning_speed
    if config.custom_min_signals:
        adj.custom_min_signals = config.custom_min_signals

    await db.commit()

    labels = {
        "conservative": "Conservador: aprende con 10+ señales",
        "moderate":     "Moderado: aprende con 5+ señales",
        "fast":         "Rápido: aprende con 3+ señales",
        "custom":       f"Personalizado: aprende con {config.custom_min_signals}+ señales",
    }
    return {"status": "updated", "message": labels[config.learning_speed]}


@router.post("/reset")
async def reset_learning(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resetea todos los multiplicadores a 1.0."""
    adj = await get_or_create_thresholds(current_user.id, db)

    adj.tsb_rest_multiplier      = 1.0
    adj.tsb_reduce_multiplier    = 1.0
    adj.acwr_danger_multiplier   = 1.0
    adj.acwr_caution_multiplier  = 1.0
    adj.readiness_low_multiplier = 1.0
    adj.rest_followed    = 0
    adj.rest_ignored     = 0
    adj.rest_good_ignore = 0
    adj.rest_bad_ignore  = 0
    adj.reduce_followed  = 0
    adj.reduce_ignored   = 0
    adj.increase_followed = 0
    adj.increase_ignored  = 0
    adj.total_analyzed   = 0

    await db.commit()
    return {"status": "reset", "message": "Motor reiniciado a valores base"}