# config.py
from functools import lru_cache
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator, SecretStr

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DoD obligatorios
    SECRET_KEY: SecretStr
    DATABASE_URL: SecretStr

    # Entorno y CORS
    ENV: Literal["development", "production", "test"]
    ALLOWED_ORIGINS: list[str] = []

    # Otros (con defaults / cast autom.)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: float = 2880

    SMTP_SERVER: str | None = None
    SMTP_PORT: int | None = None
    SENDER_EMAIL: str | None = None
    SENDER_PASSWORD: SecretStr | None = None

    VERIFICATION_LINK: str | None = None
    BASE_URL: str = "http://127.0.0.1:5500"

    # Career Mode Scoring
    MAX_STAGE_SCORE_SAFE: int = 500  # Fallback si no se puede inferir total_flags
    MAX_FLAGS_PER_STAGE: int = 30

    @field_validator("DATABASE_URL")
    @classmethod
    def normalize_db_url(cls, v):
        is_secret = isinstance(v, SecretStr)
        raw = v.get_secret_value() if is_secret else v
        if raw.startswith("postgres://"):
            raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
        return SecretStr(raw) if is_secret else raw

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def split_origins(cls, v):
        # Permite "a,b,c" en envs además de JSON
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v or []

    @model_validator(mode="after")
    def validate_cors(self):
        if self.ENV == "production":
            if not self.ALLOWED_ORIGINS:
                raise ValueError("ALLOWED_ORIGINS vacío en producción.")
            if "*" in self.ALLOWED_ORIGINS:
                raise ValueError("CORS wildcard (*) prohibido en producción (DoD).")
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
