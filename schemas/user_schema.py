from datetime import date

from pydantic import BaseModel, EmailStr, ConfigDict, Field, field_validator
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
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    username: Optional[str] = Field(default=None, min_length=3, max_length=24)
    full_name: Optional[str] = Field(default=None, max_length=120)
    profile_image: Optional[bytes] = None
    country: Optional[str] = Field(default=None, max_length=80)
    ranking_alias: Optional[str] = Field(default=None, max_length=24)
    ranking_region: Optional[str] = Field(default=None, max_length=20)

    @field_validator("username")
    @classmethod
    def username_cannot_be_blank(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("username cannot be blank")
        return value

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
    ranking_alias: Optional[str] = None
    ranking_region: Optional[str] = None

class ResendEmail(BaseModel):
    email: EmailStr
    model_config = ConfigDict(from_attributes=True)


class PasswordResetConfirm(BaseModel):
    token: str = Field(min_length=1, max_length=4096)
    password: str = Field(min_length=8, max_length=128)

class UserMeResponse(BaseModel):
    id: int
    email: EmailStr
    username: str
    full_name: Optional[str] = None
    is_active: bool
    country: Optional[str] = None
    ranking_alias: Optional[str] = None
    ranking_region: Optional[str] = None
    profile_image_url: str
    onboarding_completed: bool = False
    model_config = ConfigDict(from_attributes=True)

    model_config = ConfigDict(from_attributes=True)

class OnboardingUpdate(BaseModel):
    onboarding_completed: bool
