from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, LargeBinary, Date, DateTime, Float, UniqueConstraint, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime

from db import database

class User(database.Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    username = Column(String, unique=True, index=True)
    full_name = Column(String, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    profile_image = Column(LargeBinary, nullable=True)
    overall_score = relationship("OverallScoreTable", back_populates='user', cascade="all, delete")
    country = Column(String, nullable=True)
    onboarding_completed = Column(Boolean, default=False, nullable=False)
    
    # Career mode relationships (modo carrera only - regiones does NOT persist here)
    career_stats = relationship("CareerUserStats", back_populates="user", uselist=False, cascade="all, delete")
    stage_bests = relationship("StageBest", back_populates="user", cascade="all, delete")
    stage_runs = relationship("StageRun", back_populates="user", cascade="all, delete")
    

class OverallScoreTable(database.Base):
    __tablename__ = "overall_score_table"

    id = Column(Integer, primary_key=True)
    max_score = Column(Integer)
    last_score = Column(Integer)
    date_max_score = Column(DateTime)
    date_last_score = Column(Date)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))
    region_key = Column(String, default="career", index=True, nullable=False)
    country_code = Column(String, index=True, nullable=True)

    __table_args__ = (
        UniqueConstraint('user_id', 'region_key', name='uix_user_region'),
    )

    user = relationship("User", back_populates='overall_score')


class DailyChallenge(database.Base):
    __tablename__ = "daily_challenges"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, unique=True, index=True, nullable=False)
    country_name = Column(String, nullable=False)
    country_code = Column(String, nullable=False)  # cca3
    flag_image_bytes = Column(LargeBinary, nullable=False)
    
    # Educational & Hint Data
    region = Column(String, nullable=True)
    subregion = Column(String, nullable=True)
    capital = Column(String, nullable=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    population = Column(Integer, nullable=True)
    languages = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class DailyAttempt(database.Base):
    __tablename__ = "daily_attempts"

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(Integer, ForeignKey("daily_challenges.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    anonymous_id = Column(String, nullable=True, index=True)
    attempts_used = Column(Integer, default=0)
    solved = Column(Boolean, default=False)
    failed = Column(Boolean, default=False)
    solved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    challenge = relationship("DailyChallenge")
    guesses = relationship("DailyGuess", back_populates="attempt", cascade="all, delete-orphan")


class DailyGuess(database.Base):
    __tablename__ = "daily_guesses"

    id = Column(Integer, primary_key=True, index=True)
    attempt_id = Column(Integer, ForeignKey("daily_attempts.id"), nullable=False)
    guess_text = Column(String, nullable=False)
    is_correct = Column(Boolean, nullable=False)
    attempt_number = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    attempt = relationship("DailyAttempt", back_populates="guesses")


# =============================================================================
# CAREER MODE TABLES
# Nota: Estas tablas son exclusivas para modo carrera.
# Modo por regiones NO persiste puntaje ni estadísticas aquí.
# overall_score_table (legacy) queda intacta para compatibilidad con modo regiones.
# =============================================================================

class CareerUserStats(database.Base):
    """
    Estadísticas agregadas de carrera por usuario (1 fila por user).
    Facilita ORDER BY para ranking: stages_completed DESC, total_score DESC,
    total_hints_used ASC, total_time_seconds ASC, last_activity_at ASC.
    """
    __tablename__ = "career_user_stats"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    stages_completed = Column(Integer, default=0, nullable=False)
    total_score = Column(Integer, default=0, nullable=False)
    total_hints_used = Column(Integer, default=0, nullable=False)
    total_time_seconds = Column(Integer, default=0, nullable=False)
    last_activity_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index('ix_career_ranking', 
              stages_completed.desc(), 
              total_score.desc(), 
              total_hints_used.asc(), 
              total_time_seconds.asc(),
              last_activity_at.asc()),
    )

    user = relationship("User", back_populates="career_stats")


class StageBest(database.Base):
    """
    Mejor resultado por usuario y etapa (evitar farming).
    Unique constraint en (user_id, stage_id).
    """
    __tablename__ = "stage_best"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_id = Column(String, nullable=False, index=True)
    score = Column(Integer, nullable=False)
    hints_used = Column(Integer, default=0, nullable=False)
    time_seconds = Column(Integer, nullable=False)
    groups = Column(JSONB, nullable=True)  # Detalles del mejor intento
    achieved_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'stage_id', name='uix_user_stage_best'),
    )

    user = relationship("User", back_populates="stage_bests")


class StageRun(database.Base):
    """
    Historial de corridas individuales por etapa.
    Index compuesto en (user_id, stage_id, created_at) para queries de historial.
    """
    __tablename__ = "stage_runs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    stage_id = Column(String, nullable=False)
    score = Column(Integer, nullable=False)
    hints_used = Column(Integer, default=0, nullable=False)
    time_seconds = Column(Integer, nullable=False)
    groups = Column(JSONB, nullable=True)  # Detalles de esta corrida
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    __table_args__ = (
        Index('ix_stage_runs_user_stage_created', 'user_id', 'stage_id', 'created_at'),
    )

    user = relationship("User", back_populates="stage_runs")
