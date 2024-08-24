from datetime import date

from pydantic import BaseModel, EmailStr
from typing import Optional
# from schemas.overall_score_schema import OverallScore



class UserBase(BaseModel):
    email: str


class UserCreate(UserBase):
    password: str
    username: str
    full_name: str | None = None
    profile_image: Optional[bytes] = None


class User(UserBase):
    id: int
    is_active: bool
    profile_image: Optional[bytes] = None

    class Config:
        orm_mode = True

class OverallScore(BaseModel):
    max_score: int
    last_score: int
    date_max_score: Optional[date] = None
    date_last_score: Optional[date] = None
    user: Optional[User] = None

    class Config:
        orm_mode = True


class ScoreRequest(BaseModel):
    score: int
