"""
MORPHY - Compliance Engine
Infiere automáticamente si el atleta siguió la recomendación del día
comparando la decisión generada vs la actividad registrada en Strava.
"""
import logging
from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import uuid

from app.models.models import User, Activity

logger = logging.getLogger(__name__)


def karvonen_zone(avg_hr: float, max_hr: float, rest_hr: float) -> int:
    """
    Clasifica HR promedio en zona Karvonen (1-5).
    Retorna 0 si no hay datos suficientes.
    """
    if avg_hr <= 0 or max_hr <= rest_hr:
        return 0
    hrr = max_hr - rest_hr
    pct = (avg_hr - rest_hr) / hrr

    if pct < 0.50:
        return 1   # Por debajo de Z1 — muy suave
    elif pct < 0.60:
        return 1   # Z1 Recuperación
    elif pct < 0.70:
        return 2   # Z2 Aeróbico base
    elif pct < 0.80:
        return 3   # Z3 Tempo
    elif pct < 0.90:
        return 4   # Z4 Umbral
    else:
        return 5   # Z5 VO2max


def infer_compliance(
    decision_action: str,
    activity_avg_hr: Optional[float],
    activity_duration_min: Optional[float],
    max_hr: float,
    rest_hr: float,
) -> tuple[bool, str, str]:
    """
    Determina si el atleta siguió la recomendación.

    Returns:
        (followed: bool, zone: str, reason: str)
    """
    action = decision_action.lower()

    # Sin actividad ese día
    if not activity_avg_hr or activity_duration_min is None:
        if action == "rest":
            return True, "none", "No hubo actividad — REST correcto"
        else:
            return False, "none", f"No hubo actividad pero se recomendó {action.upper()}"

    zone = karvonen_zone(activity_avg_hr, max_hr, rest_hr)
    zone_label = f"Z{zone}" if zone > 0 else "Z?"

    # Lógica de compliance por acción
    if action == "rest":
        # REST: tolerar Z1 corto (≤25 min) como "descanso activo"
        if zone <= 1 and (activity_duration_min or 0) <= 25:
            return True, zone_label, f"Actividad muy suave en {zone_label} ({activity_duration_min:.0f}min) — REST respetado"
        else:
            return False, zone_label, f"Entrenó en {zone_label} ({activity_duration_min:.0f}min) cuando se recomendó REST"

    elif action == "reduce":
        # REDUCE: debe estar en Z1-Z2
        if zone <= 2:
            return True, zone_label, f"Intensidad correcta en {zone_label} — REDUCE seguido"
        else:
            return False, zone_label, f"Entrenó en {zone_label} cuando se recomendó REDUCE (objetivo: Z1-Z2)"

    elif action == "maintain":
        # MAINTAIN: Z2-Z3 es correcto, Z1 es aceptable, Z4+ es exceso
        if 1 <= zone <= 3:
            return True, zone_label, f"Intensidad adecuada en {zone_label} — MAINTAIN seguido"
        else:
            return False, zone_label, f"Entrenó en {zone_label} — exceso respecto a MAINTAIN (objetivo: Z2-Z3)"

    elif action == "increase":
        # INCREASE: Z3+ es correcto
        if zone >= 3:
            return True, zone_label, f"Buena intensidad en {zone_label} — INCREASE seguido"
        else:
            return False, zone_label, f"Intensidad baja en {zone_label} cuando se recomendó INCREASE (objetivo: Z3+)"

    # Fallback
    return True, zone_label, f"Actividad registrada en {zone_label}"


async def run_compliance_inference(
    db: AsyncSession,
    user: User,
    days_back: int = 7,
) -> dict:
    """
    Corre inferencia de compliance para los últimos N días.
    Para cada día con actividad, reconstruye la decisión que habría
    generado MORPHY y compara con lo que el atleta hizo.

    Se ejecuta después de sync/enrich.
    """
    from app.routers.decision import calculate_athlete_state, generate_decision

    max_hr = float(user.max_hr or 182)
    rest_hr = float(user.rest_hr or 50)

    now = datetime.utcnow()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Cargar actividades de los últimos 42 días (necesarios para ATL/CTL)
    cutoff_42 = today - timedelta(days=42)
    act_result = await db.execute(
        select(Activity)
        .where(Activity.user_id == user.id)
        .where(Activity.start_date >= cutoff_42)
        .order_by(Activity.start_date.asc())
    )
    all_activities = act_result.scalars().all()

    inferred = 0
    skipped = 0
    errors = 0

    for days_ago in range(days_back, 0, -1):
        target_date = today - timedelta(days=days_ago)
        day_start = target_date
        day_end = target_date + timedelta(days=1)

        # Actividades de ese día específico
        day_activities = [
            a for a in all_activities
            if a.start_date and day_start <= a.start_date.replace(tzinfo=None) < day_end
        ]

        # Actividades disponibles hasta ese día (para calcular estado)
        activities_until_day = [
            a for a in all_activities
            if a.start_date and a.start_date.replace(tzinfo=None) < day_end
        ]

        if not activities_until_day:
            continue

        try:
            # Calcular estado fisiológico de ese día
            state = calculate_athlete_state(activities_until_day, max_hr, rest_hr)
            decision = generate_decision(state, user.name or "")
            decision_action = decision["action"]

            # Actividad principal del día (la de mayor duración si hay varias)
            main_activity = max(day_activities, key=lambda a: a.duration_min or 0) if day_activities else None
            avg_hr = float(main_activity.avg_hr) if main_activity and main_activity.avg_hr else None
            duration = float(main_activity.duration_min) if main_activity and main_activity.duration_min else None

            # Inferir compliance
            followed, zone, reason = infer_compliance(
                decision_action, avg_hr, duration, max_hr, rest_hr
            )

            # Buscar si ya existe un registro para este día
            from app.models.models import DecisionFeedback
            existing = await db.execute(
                select(DecisionFeedback).where(
                    and_(
                        DecisionFeedback.user_id == user.id,
                        DecisionFeedback.date >= day_start,
                        DecisionFeedback.date < day_end,
                        DecisionFeedback.auto_inferred == True,
                    )
                )
            )
            existing_record = existing.scalar_one_or_none()

            if existing_record:
                # Actualizar registro existente
                existing_record.followed = followed
                existing_record.action = decision_action
                existing_record.acwr = state["acwr"]
                existing_record.readiness = state["readiness_score"]
                existing_record.tsb = state["stress_balance_tsb"]
                existing_record.activity_avg_hr = avg_hr
                existing_record.activity_duration_min = duration
                existing_record.detected_zone = zone
                existing_record.compliance_reason = reason
            else:
                # Crear nuevo registro automático
                new_record = DecisionFeedback(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    date=target_date,
                    action=decision_action,
                    followed=followed,
                    acwr=state["acwr"],
                    readiness=state["readiness_score"],
                    tsb=state["stress_balance_tsb"],
                    note=None,
                    auto_inferred=True,
                    activity_avg_hr=avg_hr,
                    activity_duration_min=duration,
                    detected_zone=zone,
                    compliance_reason=reason,
                )
                db.add(new_record)

            inferred += 1
            logger.info(f"Compliance {target_date.date()}: {decision_action} → {zone} → followed={followed} | {reason}")

        except Exception as e:
            logger.error(f"Error inferring compliance for {target_date.date()}: {e}")
            errors += 1
            continue

    await db.commit()

    return {
        "days_analyzed": days_back,
        "inferred": inferred,
        "skipped": skipped,
        "errors": errors,
    }