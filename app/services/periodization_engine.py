from dataclasses import dataclass
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, case
from datetime import datetime, timedelta
from app.models.models import DecisionFeedback


@dataclass
class TrainingPhase:
    phase: str
    label: str
    emoji: str
    description: str
    recommendation: str
    confidence: float
    metrics_used: dict


async def detect_phase(
    user_id: str,
    atl: float,
    ctl: float,
    tsb: float,
    acwr: float,
    db: AsyncSession
) -> TrainingPhase:
    cutoff = (datetime.utcnow() - timedelta(days=7)).date()

    # Compliance general 7 días
    result = await db.execute(
        select(
            func.count(DecisionFeedback.id).label("total"),
            func.sum(case((DecisionFeedback.followed == True, 1), else_=0)).label("followed_count")
        ).where(
            DecisionFeedback.user_id == user_id,
            DecisionFeedback.auto_inferred == True,
            DecisionFeedback.date >= cutoff
        )
    )
    row = result.one()
    total = row.total or 0
    followed = row.followed_count or 0
    compliance_7d = (followed / total * 100) if total > 0 else 50.0

    # Compliance específica en días de aumento
    result2 = await db.execute(
        select(
            func.count(DecisionFeedback.id).label("total"),
            func.sum(case((DecisionFeedback.followed == True, 1), else_=0)).label("followed_count")
        ).where(
            DecisionFeedback.user_id == user_id,
            DecisionFeedback.auto_inferred == True,
            DecisionFeedback.action == "increase",
            DecisionFeedback.date >= cutoff
        )
    )
    row2 = result2.one()
    inc_total = row2.total or 0
    inc_followed = row2.followed_count or 0
    increase_compliance = (inc_followed / inc_total * 100) if inc_total > 0 else 50.0

    metrics = {
        "tsb": tsb,
        "acwr": acwr,
        "ctl": ctl,
        "atl": atl,
        "compliance_7d": round(compliance_7d, 1),
        "increase_compliance": round(increase_compliance, 1),
        "days_with_data": total
    }

    # DESCARGA
    if tsb < -20 or acwr > 1.3 or compliance_7d < 35:
        reasons = []
        if tsb < -20:
            reasons.append(f"TSB en {tsb:.1f} (fatiga alta)")
        if acwr > 1.3:
            reasons.append(f"ACWR en {acwr:.2f} (riesgo lesión)")
        if compliance_7d < 35:
            reasons.append(f"Compliance {compliance_7d:.0f}%")
        return TrainingPhase(
            phase="descarga",
            label="Semana de Descarga",
            emoji="🔴",
            description=f"Tu cuerpo necesita recuperación. {' | '.join(reasons)}.",
            recommendation="Prioriza Z1-Z2, sesiones < 45 min, mucho sueño.",
            confidence=min(0.95, 0.70 + abs(tsb) * 0.01),
            metrics_used=metrics
        )

    
    
    # PICO
    if ctl >= 60 and tsb >= -5 and compliance_7d >= 70:
        return TrainingPhase(
            phase="pico",
            label="Forma de Pico",
            emoji="🔵",
            description=f"CTL {ctl:.0f}, TSB {tsb:.1f}, compliance {compliance_7d:.0f}%. Forma de competencia.",
            recommendation="Mantén ritmo, agrega 1 sesión de calidad semanal. Ideal para competir.",
            confidence=0.85,
            metrics_used=metrics
        )