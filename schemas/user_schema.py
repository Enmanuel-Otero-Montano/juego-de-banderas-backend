from datetime import date

from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional



class UserBase(BaseModel):
    email: EmailStr    


class UserCreate(UserBase):
    password: str
    username: str
    full_name: str | None = None
    profile_image: Optional[bytes] = None


class User(UserBase):
    id: int
    is_active: bool
    profile_image: Optional[bytes] = None

    model_config = ConfigDict(from_attributes=True)

class UserProfileUpdate(BaseModel):
    username: Optional[str] = None
    full_name: Optional[str] = None
    profile_image: Optional[bytes] = None
    country: Optional[str] = None

class UserRegisterResponse(BaseModel):
    id: int
    email: EmailStr
    username: str
    full_name: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

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

    model_config = ConfigDict(from_attributes=True)

class ScoreRequest(BaseModel):
    score: int

class ResendEmail(BaseModel):
    email: EmailStr
    model_config = ConfigDict(from_attributes=True)

class UserMeResponse(BaseModel):
    id: int
    email: EmailStr
    username: str
    full_name: Optional[str] = None
    is_active: bool
    country: Optional[str] = None
    profile_image_url: str
    onboarding_completed: bool = False
    model_config = ConfigDict(from_attributes=True)

class OverallScorePublic(BaseModel):
    username: str
    max_score: int
    date_max_score: Optional[date] = None
    model_config = ConfigDict(from_attributes=True)

class OnboardingUpdate(BaseModel):
    onboarding_completed: bool

