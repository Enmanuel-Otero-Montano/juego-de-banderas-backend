"""Provisiona dos cuentas verificadas para el smoke test de Carrera.

No hace nada si las contraseñas no están configuradas. Esto permite mantener
el script en la rama sin crear usuarios fuera del entorno de staging.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import database, models
from main import get_password_hash


PLAYERS = (
    (
        "staging_racer_one",
        "staging-racer-one@atlasflags.invalid",
        "STAGING_PLAYER_ONE_PASSWORD",
        "Staging Racer One",
    ),
    (
        "staging_racer_two",
        "staging-racer-two@atlasflags.invalid",
        "STAGING_PLAYER_TWO_PASSWORD",
        "Staging Racer Two",
    ),
)


def main() -> None:
    passwords = {name: os.environ.get(name, "") for _, _, name, _ in PLAYERS}
    if not any(passwords.values()):
        return
    if not all(passwords.values()):
        raise SystemExit("Deben configurarse ambas contraseñas de jugadores staging.")

    session = database.SessionLocal()
    try:
        for username, email, password_env, full_name in PLAYERS:
            password = passwords[password_env]
            if not 8 <= len(password.encode("utf-8")) <= 72:
                raise SystemExit(f"{password_env} debe tener entre 8 y 72 bytes UTF-8.")

            user = session.query(models.User).filter(
                (models.User.username == username) | (models.User.email == email)
            ).first()
            if user and (user.username != username or user.email != email):
                raise SystemExit(f"El alias o correo de {username} ya está ocupado.")
            if user is None:
                user = models.User(username=username, email=email)
                session.add(user)

            user.full_name = full_name
            user.hashed_password = get_password_hash(password)
            user.is_active = True
            user.is_verified = True

        session.commit()
        print("Cuentas de Carrera staging provisionadas.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
