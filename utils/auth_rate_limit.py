"""Límites de autenticación compartidos usando la base PostgreSQL existente."""
from datetime import timedelta
from hashlib import sha256

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from db.models import AuthRateLimit, utc_now


def _key(scope: str, subject: str, client_ip: str) -> str:
    material = f"{scope}\x1f{subject.strip().lower()}\x1f{client_ip}".encode()
    return sha256(material).hexdigest()


def consume_auth_attempt(db: Session, *, scope: str, subject: str, client_ip: str, maximum: int, window_seconds: int) -> None:
    now = utc_now()
    key = _key(scope, subject, client_ip)
    row = db.query(AuthRateLimit).filter(AuthRateLimit.key == key).with_for_update().first()
    if row is None:
        row = AuthRateLimit(key=key, window_started_at=now, attempts=1)
        db.add(row)
    elif now - row.window_started_at >= timedelta(seconds=window_seconds):
        row.window_started_at, row.attempts = now, 1
    elif row.attempts >= maximum:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many attempts. Try again later.")
    else:
        row.attempts += 1
    db.commit()
