from datetime import date

from pydantic import BaseModel, EmailStr
from typing import Optional



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

class UserProfileUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    profile_image: Optional[bytes] = None
    country: Optional[str] = None

class UserRegisterResponse(UserBase):
    full_name: str
    hashed_password: str

class UserEditProfileCurrentData(BaseModel):
    # Clase que se devuelve en el endpoint que solicita los datos para cargar en el formulario de edición de datos
    username: Optional[str] = None
    full_name: Optional[str] = None
    country: Optional[str] = None

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
