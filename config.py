# config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DoD obligatorios
    SECRET_KEY: str
    DATABASE_URL: str

    # Otros (con defaults / cast autom.)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: float = 2880

    SMTP_SERVER: str | None = None
    SMTP_PORT: int | None = None
    SENDER_EMAIL: str | None = None
    SENDER_PASSWORD: str | None = None

    VERIFICATION_LINK: str | None = None
    BASE_URL: str = "http://127.0.0.1:5500"

    @field_validator("DATABASE_URL")
    @classmethod
    def normalize_db_url(cls, v: str) -> str:
        # Corrige URLs antiguas postgres:// -> postgresql+psycopg2://
        if v.startswith("postgres://"):
            v = v.replace("postgres://", "postgresql+psycopg2://", 1)
        return v

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
