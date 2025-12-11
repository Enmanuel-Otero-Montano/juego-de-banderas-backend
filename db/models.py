from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, LargeBinary, Date
from sqlalchemy.orm import relationship

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
    

class OverallScoreTable(database.Base):
    __tablename__ = "overall_score_table"

    id = Column(Integer, primary_key=True)
    max_score = Column(Integer)
    last_score = Column(Integer)
    date_max_score = Column(Date)
    date_last_score = Column(Date)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    user = relationship("User", back_populates='overall_score')
