"""
Sprint 11: Adaptive Learning Engine
Analiza feedback histórico y ajusta umbrales personalizados por usuario.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.models import DecisionFeedback, AthleteThresholdAdjustment

# Mínimo de señales por velocidad
MIN_SIGNALS = {"conservative": 10, "moderate": 5, "fast": 3}

# Cuánto se mueve el multiplicador por señal fuerte
STEP = 0.03
MULT_MIN, MULT_MAX = 0.70, 1.30


def clamp(v: float) -> float:
    return max(MULT_MIN, min(MULT_MAX, v))


async def get_or_create_thresholds(user_id: int, db: AsyncSession) -> AthleteThresholdAdjustment:
    result = await db.execute(
        select(AthleteThresholdAdjustment).where(AthleteThresholdAdjustment.user_id == user_id)
    )
    adj = result.scalar_one_or_none()
    if not adj:
        adj = AthleteThresholdAdjustment(user_id=user_id)
        db.add(adj)
        await db.flush()
    return adj


def get_min_signals(adj: AthleteThresholdAdjustment) -> int:
    if adj.learning_speed == "custom":
        return adj.custom_min_signals or 5
    return MIN_SIGNALS.get(adj.learning_speed or "moderate", 5)


#async def analyze_and_update(user_id: int, db: AsyncSession) -> dict:
async def analyze_and_update(user_id: int, db: AsyncSession) -> dict:
    print(f"DEBUG analyze_and_update called with user_id: {user_id}, type: {type(user_id)}")
    """
    Analiza historial de feedback y actualiza multiplicadores.
    Llamar después de guardar cada feedback nuevo.
    """
    adj = await get_or_create_thresholds(user_id, db)
    min_signals = get_min_signals(adj)

    # Leer todo el feedback del usuario
    result = await db.execute(
        select(DecisionFeedback)
        .where(DecisionFeedback.user_id == user_id)
        .order_by(DecisionFeedback.date.desc())
        .limit(100)
    )
    feedbacks = result.scalars().all()

    if not feedbacks:
        return {"status": "no_feedback"}

    # Contar señales por tipo de acción
    signals = {
        "active_recovery": {"followed": 0, "ignored": 0},
        "easy":            {"followed": 0, "ignored": 0},
        "moderate":        {"followed": 0, "ignored": 0},
        "quality":         {"followed": 0, "ignored": 0},
    }

    # Mapear actions de la DB a los del motor
    action_map = {
        "rest": "active_recovery",
        "reduce": "easy", 
        "increase": "quality",
        "moderate": "moderate",
        "active_recovery": "active_recovery",
        "easy": "easy",
        "quality": "quality",
    }

    for f in feedbacks:
        action = action_map.get(f.action, f.action)
        if action not in signals:
            continue
        if f.followed:
            signals[action]["followed"] += 1
        else:
            signals[action]["ignored"] += 1

    # Mapear a los contadores que guardamos
    # "active_recovery" = equivalente a "rest" en el contexto del motor
    rest_total = signals["active_recovery"]["followed"] + signals["active_recovery"]["ignored"]
    reduce_total = signals["easy"]["followed"] + signals["easy"]["ignored"]
    increase_total = signals["quality"]["followed"] + signals["quality"]["ignored"]
    print(f"DEBUG signals: {signals}")
    all_total = sum(v["followed"] + v["ignored"] for v in signals.values())

    adjustments_made = {}

    # ── AJUSTE 1: ACWR danger (active_recovery) ──────────────────────────────
    # Si siempre ignora "active_recovery" → más tolerante con carga alta
    if rest_total >= min_signals:
        ignore_rate = signals["active_recovery"]["ignored"] / rest_total
        old = adj.acwr_danger_multiplier

        if ignore_rate > 0.6:
            adj.acwr_danger_multiplier = clamp(old + STEP)
            adjustments_made["acwr_danger"] = {
                "direction": "increased_tolerance",
                "old": round(old, 3),
                "new": round(adj.acwr_danger_multiplier, 3),
                "reason": f"Ignora 'active_recovery' {round(ignore_rate*100)}% del tiempo"
            }
        elif ignore_rate < 0.2 and rest_total >= min_signals * 2:
            adj.acwr_danger_multiplier = clamp(old - STEP * 0.5)
            adjustments_made["acwr_danger"] = {
                "direction": "decreased_tolerance",
                "old": round(old, 3),
                "new": round(adj.acwr_danger_multiplier, 3),
                "reason": "Sigue consistentemente las recomendaciones de recuperación"
            }

    # ── AJUSTE 2: ACWR caution (easy days) ───────────────────────────────────
    if reduce_total >= min_signals:
        ignore_rate = signals["easy"]["ignored"] / reduce_total
        old = adj.acwr_caution_multiplier

        if ignore_rate > 0.65:
            adj.acwr_caution_multiplier = clamp(old + STEP)
            adjustments_made["acwr_caution"] = {
                "direction": "increased_tolerance",
                "old": round(old, 3),
                "new": round(adj.acwr_caution_multiplier, 3),
                "reason": f"Tolera cargas elevadas sin seguir recomendaciones 'easy'"
            }
        elif ignore_rate < 0.2 and reduce_total >= min_signals * 2:
            adj.acwr_caution_multiplier = clamp(old - STEP * 0.5)
            adjustments_made["acwr_caution"] = {
                "direction": "decreased_tolerance",
                "old": round(old, 3),
                "new": round(adj.acwr_caution_multiplier, 3),
                "reason": "Perfil conservador: siempre sigue las reducciones"
            }

    # ── AJUSTE 3: TSB (readiness general) ────────────────────────────────────
    if all_total >= min_signals * 2:
        all_ignored = sum(v["ignored"] for v in signals.values())
        overall_ignore_rate = all_ignored / all_total
        old = adj.tsb_rest_multiplier

        if overall_ignore_rate > 0.7:
            adj.tsb_rest_multiplier = clamp(old + STEP * 0.5)
            adjustments_made["tsb_rest"] = {
                "direction": "increased_tolerance",
                "old": round(old, 3),
                "new": round(adj.tsb_rest_multiplier, 3),
                "reason": "Atleta con alta tolerancia general a la fatiga"
            }

    # ── Actualizar contadores ────────────────────────────────────────────────
    adj.rest_followed    = signals["active_recovery"]["followed"]
    adj.rest_ignored     = signals["active_recovery"]["ignored"]
    adj.reduce_followed  = signals["easy"]["followed"]
    adj.reduce_ignored   = signals["easy"]["ignored"]
    adj.increase_followed = signals["quality"]["followed"]
    adj.increase_ignored  = signals["quality"]["ignored"]
    adj.total_analyzed   = all_total

    db.add(adj)
    await db.commit()

    return {
        "status": "updated",
        "total_signals": all_total,
        "min_signals_required": min_signals,
        "learning_active": all_total >= min_signals,
        "adjustments_made": adjustments_made,
    }


async def get_learning_status(user_id: int, db: AsyncSession) -> dict:
    """Estado completo del motor adaptativo para el dashboard."""
    adj = await get_or_create_thresholds(user_id, db)
    await db.commit()

    min_signals = get_min_signals(adj)
    total = adj.total_analyzed or 0
    learning_active = total >= min_signals
    personalization_score = min(100, int((total / max(min_signals * 3, 1)) * 100))

    # Umbrales base × multiplicadores
    base = {"acwr_danger": 1.5, "acwr_caution": 1.3, "tsb_rest": -20.0}
    effective = {
        "acwr_danger":  round(base["acwr_danger"]  * adj.acwr_danger_multiplier,  2),
        "acwr_caution": round(base["acwr_caution"] * adj.acwr_caution_multiplier, 2),
        "tsb_rest":     round(base["tsb_rest"]     * adj.tsb_rest_multiplier,     1),
    }

    # Qué umbrales se han movido significativamente
    adjusted_thresholds = []
    if abs(adj.acwr_danger_multiplier - 1.0) > 0.05:
        adjusted_thresholds.append({
            "name": "ACWR Peligro",
            "base": 1.5,
            "effective": effective["acwr_danger"],
            "direction": "más tolerante" if adj.acwr_danger_multiplier > 1.0 else "más conservador"
        })
    if abs(adj.acwr_caution_multiplier - 1.0) > 0.05:
        adjusted_thresholds.append({
            "name": "ACWR Precaución",
            "base": 1.3,
            "effective": effective["acwr_caution"],
            "direction": "más tolerante" if adj.acwr_caution_multiplier > 1.0 else "más conservador"
        })
    if abs(adj.tsb_rest_multiplier - 1.0) > 0.05:
        adjusted_thresholds.append({
            "name": "TSB Umbral",
            "base": -20.0,
            "effective": effective["tsb_rest"],
            "direction": "más tolerante" if adj.tsb_rest_multiplier > 1.0 else "más conservador"
        })

    return {
        "learning_active": learning_active,
        "learning_speed": adj.learning_speed,
        "min_signals_required": min_signals,
        "total_decisions_analyzed": total,
        "personalization_score": personalization_score,
        "signals": {
            "active_recovery": {"followed": adj.rest_followed,     "ignored": adj.rest_ignored},
            "easy":            {"followed": adj.reduce_followed,   "ignored": adj.reduce_ignored},
            "quality":         {"followed": adj.increase_followed, "ignored": adj.increase_ignored},
        },
        "adjusted_thresholds": adjusted_thresholds,
        "thresholds_effective": effective,
        "multipliers": {
            "acwr_danger":  round(adj.acwr_danger_multiplier,  3),
            "acwr_caution": round(adj.acwr_caution_multiplier, 3),
            "tsb_rest":     round(adj.tsb_rest_multiplier,     3),
        }
    }


async def get_effective_thresholds(user_id: int, db: AsyncSession) -> dict:
    """
    Retorna los umbrales efectivos del usuario.
    Usado por el DecisionEngine para personalizar decisiones.
    """
    adj = await get_or_create_thresholds(user_id, db)
    min_signals = get_min_signals(adj)
    is_personalized = (adj.total_analyzed or 0) >= min_signals

    return {
        "acwr_danger":    1.5  * adj.acwr_danger_multiplier,
        "acwr_caution":   1.3  * adj.acwr_caution_multiplier,
        "tsb_fatigued":  -15.0 * adj.tsb_rest_multiplier,
        "is_personalized": is_personalized,
        "total_analyzed": adj.total_analyzed or 0,
    }