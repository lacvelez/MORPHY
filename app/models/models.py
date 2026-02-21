import uuid
from datetime import datetime
from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, ForeignKey, BigInteger
from sqlalchemy.dialects.postgresql import UUID
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=True)
    name = Column(String, nullable=False)
    age = Column(Integer, nullable=True)
    weight_kg = Column(Float, nullable=True)
    height_cm = Column(Float, nullable=True)
    sex = Column(String, nullable=True)
    max_hr = Column(Integer, nullable=True)
    rest_hr = Column(Integer, nullable=True)
    primary_sport = Column(String, default="Run")
    experience_level = Column(String, default="intermediate")
    created_at = Column(DateTime, default=datetime.utcnow)


class Integration(Base):
    __tablename__ = "integrations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    provider = Column(String, nullable=False)
    athlete_id = Column(BigInteger, nullable=True)
    access_token = Column(String, nullable=False)
    refresh_token = Column(String, nullable=False)
    expires_at = Column(BigInteger, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Activity(Base):
    __tablename__ = "activities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    strava_id = Column(BigInteger, unique=True, nullable=True)
    name = Column(String, nullable=False)
    activity_type = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    distance_km = Column(Float, default=0)
    duration_min = Column(Float, default=0)
    elevation_m = Column(Float, default=0)
    avg_hr = Column(Integer, nullable=True)
    max_hr = Column(Integer, nullable=True)
    avg_pace = Column(Float, nullable=True)
    calories = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DecisionFeedback(Base):
    __tablename__ = "decision_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    date = Column(DateTime, nullable=False)
    action = Column(String, nullable=False)      # "rest", "reduce", "maintain", "increase"
    followed = Column(Boolean, nullable=False)
    acwr = Column(Float, nullable=True)
    readiness = Column(Float, nullable=True)
    tsb = Column(Float, nullable=True)
    note = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)