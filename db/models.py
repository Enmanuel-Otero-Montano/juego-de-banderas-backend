from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, LargeBinary, Date, DateTime, Float, UniqueConstraint, Index, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from db import database


def utc_now() -> datetime:
    """UTC sin zona para columnas DateTime históricamente naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class User(database.Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True)
    username = Column(String, unique=True)
    full_name = Column(String)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    profile_image = Column(LargeBinary, nullable=True)
    country = Column(String, nullable=True)
    # Datos públicos del ranking. Nunca se usa full_name en la clasificación.
    ranking_alias = Column(String, nullable=True)
    ranking_region = Column(String, nullable=True, index=True)
    onboarding_completed = Column(Boolean, default=False, nullable=False)
    
    # Career mode relationships
    career_stats = relationship("CareerUserStats", back_populates="user", uselist=False, cascade="all, delete")
    stage_bests = relationship("StageBest", back_populates="user", cascade="all, delete")
    stage_runs = relationship("StageRun", back_populates="user", cascade="all, delete")
    career_attempts = relationship("CareerAttempt", back_populates="user", cascade="all, delete")
    career_season_profiles = relationship("CareerSeasonProfile", back_populates="user", cascade="all, delete")


class DailyChallenge(database.Base):
    __tablename__ = "daily_challenges"

    id = Column(Integer, primary_key=True)
    date = Column(Date, unique=True, nullable=False)
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

    created_at = Column(DateTime, default=utc_now)


class DailyAttempt(database.Base):
    __tablename__ = "daily_attempts"

    id = Column(Integer, primary_key=True)
    challenge_id = Column(Integer, ForeignKey("daily_challenges.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    anonymous_id = Column(String, nullable=True, index=True)
    attempts_used = Column(Integer, default=0)
    solved = Column(Boolean, default=False)
    failed = Column(Boolean, default=False)
    solved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    challenge = relationship("DailyChallenge")
    guesses = relationship("DailyGuess", back_populates="attempt", cascade="all, delete-orphan")


class DailyGuess(database.Base):
    __tablename__ = "daily_guesses"

    id = Column(Integer, primary_key=True)
    attempt_id = Column(Integer, ForeignKey("daily_attempts.id"), nullable=False)
    guess_text = Column(String, nullable=False)
    is_correct = Column(Boolean, nullable=False)
    attempt_number = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utc_now)

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
    stages_completed = Column(Integer, default=0, server_default='0', nullable=False)
    total_score = Column(Integer, default=0, server_default='0', nullable=False)
    total_hints_used = Column(Integer, default=0, server_default='0', nullable=False)
    total_time_seconds = Column(Integer, default=0, server_default='0', nullable=False)
    total_mistakes = Column(Integer, default=0, server_default='0', nullable=False)
    last_activity_at = Column(DateTime, default=utc_now, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        Index('ix_career_ranking', 
              stages_completed.desc(), 
              total_score.desc(), 
              total_hints_used.asc(), 
              total_time_seconds.asc(),
              last_activity_at.asc()),
    )

    user = relationship("User", back_populates="career_stats")


class CareerSeasonProfile(database.Base):
    """País y región inmutables de un jugador dentro de una temporada."""
    __tablename__ = "career_season_profiles"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    season_id = Column(String, primary_key=True)
    country = Column(String, nullable=False)
    region = Column(String, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    user = relationship("User", back_populates="career_season_profiles")


class CareerAttempt(database.Base):
    """Intento clasificatorio de un solo uso emitido antes de empezar a jugar."""
    __tablename__ = "career_attempts"

    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    season_id = Column(String, nullable=False, index=True)
    ruleset_version = Column(Integer, nullable=False)
    content_version = Column(Integer, nullable=False)
    stage_id = Column(String, nullable=False)
    route_position = Column(Integer, nullable=False)
    difficulty = Column(String, nullable=False)
    country_codes = Column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    app_version = Column(String, nullable=True)
    started_at = Column(DateTime, default=utc_now, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=True)
    raw_submission = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)

    __table_args__ = (
        Index('ix_career_attempt_user_started', 'user_id', 'started_at'),
    )

    user = relationship("User", back_populates="career_attempts")


class StageBest(database.Base):
    """
    Mejor resultado por usuario y etapa (evitar farming).
    Unique constraint en (user_id, stage_id).
    """
    __tablename__ = "stage_best"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    stage_id = Column(String, nullable=False, index=True)
    route_position = Column(Integer, nullable=True)
    season_id = Column(String, default='season-1', server_default='season-1', nullable=False, index=True)
    ruleset_version = Column(Integer, default=1, server_default='1', nullable=False)
    content_version = Column(Integer, default=1, server_default='1', nullable=False)
    score = Column(Integer, nullable=False)
    mistakes = Column(Integer, default=0, server_default='0', nullable=False)
    difficulty = Column(String, default='normal', server_default='normal', nullable=False, index=True)
    hints_used = Column(Integer, default=0, server_default='0', nullable=False)
    time_seconds = Column(Integer, nullable=False)
    # JSON en tests/SQLite y JSONB en PostgreSQL. Esto permite probar el
    # contrato completo de la API sin cambiar el tipo usado en producción.
    groups = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    achieved_at = Column(DateTime, default=utc_now, nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'stage_id', 'difficulty', 'season_id', name='uix_user_stage_best_season'),
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
    route_position = Column(Integer, nullable=True)
    season_id = Column(String, default='season-1', server_default='season-1', nullable=False, index=True)
    ruleset_version = Column(Integer, default=1, server_default='1', nullable=False)
    content_version = Column(Integer, default=1, server_default='1', nullable=False)
    attempt_id = Column(String, ForeignKey("career_attempts.id", ondelete="SET NULL"), nullable=True, unique=True)
    passed = Column(Boolean, default=False, server_default='false', nullable=False)
    correct_answers = Column(Integer, default=0, server_default='0', nullable=False)
    score = Column(Integer, nullable=False)
    mistakes = Column(Integer, default=0, server_default='0', nullable=False)
    difficulty = Column(String, default='normal', server_default='normal', nullable=False, index=True)
    hints_used = Column(Integer, default=0, server_default='0', nullable=False)
    time_seconds = Column(Integer, nullable=False)
    groups = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    answers = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)

    __table_args__ = (
        Index('ix_stage_runs_user_stage_created', 'user_id', 'stage_id', 'created_at'),
    )

    user = relationship("User", back_populates="stage_runs")
