"""Bloquea un staging de Carrera inseguro o conectado por error a placeholders."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings


def main() -> None:
    errors: list[str] = []
    if settings.ENV != "production":
        errors.append("ENV debe ser production para reproducir el comportamiento real")
    if not settings.RACE_MODE_ENABLED:
        errors.append("RACE_MODE_ENABLED debe ser true")
    if not settings.ANDROID_APP_LINK_PACKAGE_NAME.endswith(".staging"):
        errors.append("ANDROID_APP_LINK_PACKAGE_NAME debe terminar en .staging")

    for name, value in {
        "RACE_INVITE_BASE_URL": settings.RACE_INVITE_BASE_URL,
        "BASE_URL": settings.BASE_URL,
        "SMTP_SERVER": settings.SMTP_SERVER,
    }.items():
        lowered = (value or "").lower()
        if any(marker in lowered for marker in ("replace-me", ".example", "localhost", "127.0.0.1")):
            errors.append(f"{name} conserva un placeholder o destino local")

    invite_url = urlparse(settings.RACE_INVITE_BASE_URL or "")
    if invite_url.path not in ("", "/") or invite_url.query or invite_url.fragment:
        errors.append("RACE_INVITE_BASE_URL debe ser el origen público, sin ruta, query ni fragmento")

    secret = settings.SECRET_KEY.get_secret_value().lower()
    if any(marker in secret for marker in ("replace", "change", "secret-key")):
        errors.append("SECRET_KEY conserva un valor descriptivo o placeholder")

    fingerprint = settings.ANDROID_APP_LINK_SHA256_CERT_FINGERPRINT or ""
    if fingerprint and len(set(fingerprint.split(":"))) == 1:
        errors.append("ANDROID_APP_LINK_SHA256_CERT_FINGERPRINT conserva la huella de ejemplo")

    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
        value = os.environ.get(name)
        if value and value != "1":
            errors.append(f"{name} debe ser 1 mientras Carrera use difusión WebSocket en memoria")

    if errors:
        print(f"Staging bloqueado por {len(errors)} problema(s):")
        for error in errors:
            print(f"- {error}")
        raise SystemExit(1)
    print(
        "Configuración de staging válida: Carrera activa, App Links separados "
        "y un solo proceso ASGI."
    )


if __name__ == "__main__":
    main()
