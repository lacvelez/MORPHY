from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta

from app.database import get_db
from app.models.models import Activity, User
from app.services.athlete_state import AthleteStateCalculator, ActivityData
from app.services.decision_engine import DecisionEngine

router = APIRouter(prefix="/morphy", tags=["morphy"])

@router.get("/state")
async def get_athlete_state(db: AsyncSession = Depends(get_db)):
    """Calcula y devuelve el estado actual del atleta"""
    # Obtener usuario
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No user found")

    # Obtener actividades últimos 42 días
    cutoff = datetime.utcnow() - timedelta(days=42)
    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id, Activity.start_date >= cutoff)
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    # Convertir a ActivityData
    activity_data = [
        ActivityData(
            date=a.start_date,
            activity_type=a.activity_type,
            duration_min=a.duration_min,
            distance_km=a.distance_km,
            elevation_m=a.elevation_m or 0,
            avg_hr=a.avg_hr,
            max_hr=a.max_hr,
            avg_pace=a.avg_pace
        )
        for a in activities
    ]

    # Calcular estado
    calculator = AthleteStateCalculator()
    state = calculator.calculate_state(activity_data)

    return {
        "athlete": user.name,
        "calculated_at": datetime.utcnow().isoformat(),
        "state": {
            "acute_load_atl": state.acute_load,
            "chronic_load_ctl": state.chronic_load,
            "stress_balance_tsb": state.training_stress_balance,
            "acwr": state.acwr,
            "injury_risk": state.injury_risk,
            "readiness_score": state.readiness_score,
            "activities_analyzed": state.activities_count,
            "days_since_last_activity": state.days_since_last,
            "last_activity": state.last_activity_date.isoformat() if state.last_activity_date else None
        }
    }

@router.get("/decision")
async def get_decision(db: AsyncSession = Depends(get_db)):
    """Genera la decisión autónoma de entrenamiento para hoy"""
    # Obtener usuario
    result = await db.execute(select(User).limit(1))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="No user found")

    # Obtener actividades últimos 42 días
    cutoff = datetime.utcnow() - timedelta(days=42)
    result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id, Activity.start_date >= cutoff)
        .order_by(Activity.start_date.desc())
    )
    activities = result.scalars().all()

    # Convertir a ActivityData
    activity_data = [
        ActivityData(
            date=a.start_date,
            activity_type=a.activity_type,
            duration_min=a.duration_min,
            distance_km=a.distance_km,
            elevation_m=a.elevation_m or 0,
            avg_hr=a.avg_hr,
            max_hr=a.max_hr,
            avg_pace=a.avg_pace
        )
        for a in activities
    ]

    # Calcular estado + decisión
    calculator = AthleteStateCalculator()
    state = calculator.calculate_state(activity_data)
    
    engine = DecisionEngine()
    decision = engine.generate_decision(state)

    return {
        "athlete": user.name,
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "decision": {
            "action": decision.action,
            "confidence": decision.confidence,
            "headline": decision.headline,
            "reasoning": decision.reasoning,
            "suggestions": decision.suggestions
        },
        "state_summary": {
            "readiness": state.readiness_score,
            "injury_risk": state.injury_risk,
            "acwr": state.acwr,
            "fatigue": state.acute_load,
            "fitness": state.chronic_load
        }
    }