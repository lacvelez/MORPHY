from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from app.database import get_db
from app.models.models import User, DecisionFeedback
from app.dependencies import get_current_user
from app.services.learning_engine import analyze_and_update  # Sprint 11

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackPayload(BaseModel):
    followed: bool
    action: str
    acwr: Optional[float] = None
    readiness: Optional[float] = None
    tsb: Optional[float] = None
    note: Optional[str] = None


@router.post("/")
async def save_feedback(
    payload: FeedbackPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    feedback = DecisionFeedback(
        user_id=current_user.id,
        date=datetime.utcnow(),
        action=payload.action,
        followed=payload.followed,
        acwr=payload.acwr,
        readiness=payload.readiness,
        tsb=payload.tsb,
        note=payload.note,
    )
    db.add(feedback)
    await db.commit()

    # Sprint 11: disparar aprendizaje después de cada feedback
    await analyze_and_update(current_user.id, db)

    return {"status": "ok", "followed": payload.followed}


@router.get("/stats")
async def get_feedback_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total_q = await db.execute(
        select(func.count()).where(DecisionFeedback.user_id == current_user.id)
    )
    total = total_q.scalar() or 0

    followed_q = await db.execute(
        select(func.count()).where(
            DecisionFeedback.user_id == current_user.id,
            DecisionFeedback.followed == True
        )
    )
    followed = followed_q.scalar() or 0

    return {
        "total_decisions": total,
        "followed": followed,
        "ignored": total - followed,
        "follow_rate": round(followed / total * 100, 1) if total > 0 else 0
    }