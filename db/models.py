from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, LargeBinary, Date, DateTime, Float
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
    

class OverallScoreTable(database.Base):
    __tablename__ = "overall_score_table"

    id = Column(Integer, primary_key=True)
    max_score = Column(Integer)
    last_score = Column(Integer)
    date_max_score = Column(Date)
    date_last_score = Column(Date)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
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

