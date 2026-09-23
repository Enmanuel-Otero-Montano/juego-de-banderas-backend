import os

import pytest
from pydantic import ValidationError

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://testserver"]')

from config import Settings


def production_settings(**overrides):
    values = {
        "ENV": "production",
        "SECRET_KEY": "production-secret-that-is-longer-than-32-characters",
        "DATABASE_URL": "postgresql+psycopg2://atlas:secret@db/atlas",
        "ALLOWED_ORIGINS": ["https://atlas.example"],
        "SMTP_SERVER": "smtp.example",
        "SMTP_PORT": 587,
        "SENDER_EMAIL": "hello@atlas.example",
        "SENDER_PASSWORD": "mail-secret",
        "VERIFICATION_LINK": "https://atlas.example/verify",
        "BASE_URL": "https://api.atlas.example",
        "RATE_LIMIT_STORAGE_URI": "rediss://atlas:secret@redis.example:6379/0",
    }
    values.update(overrides)
    return Settings(**values)


def test_valid_production_settings_are_accepted():
    settings = production_settings(RATE_LIMIT_STORAGE_URI=None)
    assert settings.ENV == "production"
    assert settings.ALGORITHM == "HS256"
    assert settings.DAILY_MAX_ATTEMPTS == 4


@pytest.mark.parametrize(
    "overrides",
    [
        {"ALLOWED_ORIGINS": []},
        {"ALLOWED_ORIGINS": ["*"]},
        {"SECRET_KEY": "too-short"},
        {"DATABASE_URL": "sqlite:///atlas.db"},
        {"SMTP_SERVER": None},
        {"VERIFICATION_LINK": "http://atlas.example/verify"},
        {"BASE_URL": "http://api.atlas.example"},
        {"RATE_LIMIT_STORAGE_URI": "memory://"},
        {"ALGORITHM": "none"},
        {"DAILY_MAX_ATTEMPTS": 2},
        {"DAILY_MAX_ATTEMPTS": 7},
    ],
)
def test_insecure_production_settings_are_rejected(overrides):
    with pytest.raises(ValidationError):
        production_settings(**overrides)
