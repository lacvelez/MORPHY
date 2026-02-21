from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime, timedelta
import math
import logging

from app.database import get_db
from app.models.models import User, Activity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/morphy", tags=["morphy"])


def safe_round(value, digits=1):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return 0.0


def calculate_athlete_state(activities: list, max_hr: float = 182, rest_hr: float = 50) -> dict:
    now = datetime.utcnow()

    def trimp(a) -> float:
        duration = float(a.duration_min or 0)
        hr = float(a.avg_hr or 0)
        if duration <= 0:
            return 0.0
        if hr <= 0:
            return duration * 0.5
        hrr = (hr - rest_hr) / (max_hr - rest_hr)
        hrr = max(0.1, min(1.0, hrr))
        return duration * hrr * (0.64 * 2.718 ** (1.92 * hrr))

    def days_ago(a) -> float:
        if a.start_date is None:
            return 999
        act_date = a.start_date
        # Eliminar timezone si existe para comparar con utcnow()
        if hasattr(act_date, 'tzinfo') and act_date.tzinfo is not None:
            act_date = act_date.replace(tzinfo=None)
        return (now - act_date).total_seconds() / 86400

    acute_load = 0.0
    chronic_load = 0.0
    chronic_count = 0

    for a in activities:
        d = days_ago(a)
        t = trimp(a)
        if d <= 7:
            w = math.exp(-d / 7.0)
            acute_load += t * w
        if d <= 42:
            w = math.exp(-d / 42.0)
            chronic_load += t * w
            chronic_count += 1

    atl = safe_round(acute_load * (7 / max(chronic_count, 1)) * 0.6, 1)
    ctl = safe_round(chronic_load * (42 / max(chronic_count * 6, 1)) * 0.15, 1)
    tsb = safe_round(ctl - atl, 1)
    acwr = safe_round(atl / ctl, 2) if ctl > 0 else 0.0

    readiness = 100.0
    if acwr > 1.5:
        readiness -= 60
    elif acwr > 1.3:
        readiness -= 30
    elif acwr < 0.7:
        readiness -= 10
    if tsb < -20:
        readiness -= 30
    elif tsb < -10:
        readiness -= 15
    readiness = max(0.0, min(100.0, readiness))

    if readiness < 20 or acwr > 1.5:
        injury_risk = "high"
    elif readiness < 50 or acwr > 1.3:
        injury_risk = "moderate"
    else:
        injury_risk = "low"

    return {
        "acute_load_atl": atl,
        "chronic_load_ctl": ctl,
        "stress_balance_tsb": tsb,
        "acwr": acwr,
        "readiness_score": safe_round(readiness, 1),
        "injury_risk": injury_risk,
    }


def generate_decision(state: dict, athlete_name: str) -> dict:
    acwr = state["acwr"]
    readiness = state["readiness_score"]
    tsb = state["stress_balance_tsb"]
    risk = state["injury_risk"]

    if acwr > 1.5 or readiness < 20:
        return {
            "action": "rest",
            "headline": "🔴 Descanso obligatorio hoy",
            "reasoning": f"Tu ACWR actual es {acwr} y tu readiness es {round(readiness)}/100. La carga aguda supera ampliamente tu base de fitness crónico. Entrenar hoy aumentaría significativamente tu riesgo de lesión.",
            "confidence": 0.92,
            "suggestions": ["Movilidad suave 15-20 minutos máximo", "Prioriza sueño de calidad (8+ horas)", "Hidratación y nutrición de recuperación", "Retoma entrenamiento en 48-72 horas"],
        }

    if acwr > 1.3 or risk == "moderate":
        return {
            "action": "reduce",
            "headline": "🟡 Reduce intensidad hoy",
            "reasoning": f"Tu ACWR es {acwr} (zona de precaución: ideal 0.8-1.3). Balance de forma: {tsb}. Tu cuerpo necesita asimilar la carga reciente antes de acumular más.",
            "confidence": 0.85,
            "suggestions": ["Rodaje suave Zona 1-2 (40-50 min máximo)", "Mantén FC < 140 bpm durante toda la sesión", "Reduce volumen un 30-40% respecto a tu plan original", "Escucha tu cuerpo — si sientes pesadez, corta antes"],
        }

    if tsb > 5 and 0.7 <= acwr < 1.0:
        return {
            "action": "increase",
            "headline": "🔵 Puedes aumentar carga hoy",
            "reasoning": f"Tu TSB es {tsb} (descansado) y tu ACWR es {acwr}. Tienes capacidad para absorber más entrenamiento sin riesgo.",
            "confidence": 0.80,
            "suggestions": ["Sesión de calidad: intervalos, tempo o fondo largo", "Puedes extender 10-15% respecto a tu plan habitual", "Aprovecha la forma para trabajar intensidad", "Asegura recuperación activa mañana"],
        }

    if 0 < acwr < 0.7:
        return {
            "action": "increase",
            "headline": "🔵 Carga baja — aumenta gradualmente",
            "reasoning": f"Tu ACWR es {acwr}, por debajo del rango óptimo (0.8-1.3). Puedes aumentar volumen de forma segura.",
            "confidence": 0.75,
            "suggestions": ["Incrementa volumen semanal en máximo 10%", "Añade una sesión extra suave esta semana", "Foco en consistencia, no en intensidad", "Mantén el patrón durante 2-3 semanas para reconstruir base"],
        }

    return {
        "action": "maintain",
        "headline": "🟢 Entrena según tu plan",
        "reasoning": f"Tu estado es óptimo: ACWR {acwr} (rango ideal), readiness {round(readiness)}/100, TSB {tsb}. Todos los indicadores están en zona verde.",
        "confidence": 0.88,
        "suggestions": ["Ejecuta tu sesión planificada completa", "Mantén el calentamiento y enfriamiento habitual", "Monitorea tu FC durante la sesión", "Registra cómo te sientes al terminar"],
    }


@router.get("/state")
async def get_athlete_state(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="No hay usuario. Ve a /auth/strava/connect primero.")

        cutoff = datetime.utcnow() - timedelta(days=42)
        act_result = await db.execute(
            select(Activity)
            .where(Activity.user_id == user.id)
            .where(Activity.start_date >= cutoff)
            .order_by(Activity.start_date.desc())
        )
        activities = act_result.scalars().all()
        if not activities:
            raise HTTPException(status_code=404, detail="Sin actividades. Ejecuta /auth/strava/sync primero.")

        state = calculate_athlete_state(
            activities,
            max_hr=float(user.max_hr or 182),
            rest_hr=float(user.rest_hr or 50)
        )
        return {
            "athlete": user.name or user.email or "Athlete",
            "activities_analyzed": len(activities),
            "calculated_at": datetime.utcnow().isoformat(),
            "state": state,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error calculando estado")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")


@router.get("/decision")
async def get_training_decision(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="No hay usuario. Ve a /auth/strava/connect primero.")

        cutoff = datetime.utcnow() - timedelta(days=42)
        act_result = await db.execute(
            select(Activity)
            .where(Activity.user_id == user.id)
            .where(Activity.start_date >= cutoff)
            .order_by(Activity.start_date.desc())
        )
        activities = act_result.scalars().all()
        if not activities:
            raise HTTPException(status_code=404, detail="Sin actividades. Ejecuta /auth/strava/sync primero.")

        athlete_name = user.name or user.email or "Athlete"
        state = calculate_athlete_state(
            activities,
            max_hr=float(user.max_hr or 182),
            rest_hr=float(user.rest_hr or 50)
        )
        decision = generate_decision(state, athlete_name)

        return {
            "athlete": athlete_name,
            "generated_at": datetime.utcnow().isoformat(),
            "state_summary": {
                "acwr": state["acwr"],
                "readiness": state["readiness_score"],
                "tsb": state["stress_balance_tsb"],
                "risk": state["injury_risk"],
            },
            "decision": decision,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error generando decisión")
        raise HTTPException(status_code=500, detail=f"Error interno: {str(e)}")