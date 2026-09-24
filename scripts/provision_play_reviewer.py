"""Crea una cuenta de revisión para Google Play sin guardar credenciales en Git.

Ejecución desde una shell de Render con los secretos de la aplicación cargados:
    python scripts/provision_play_reviewer.py

El correo usa el dominio reservado .invalid porque no se envía ningún mensaje:
la cuenta se marca verificada sólo para que Google pueda iniciar sesión durante
la revisión. La contraseña se solicita sin eco y no se imprime.
"""

from __future__ import annotations

from getpass import getpass
from pathlib import Path
import sys

# Permite ejecutar el script como ``python scripts/provision_play_reviewer.py``
# desde la shell del servicio, sin instalar el proyecto como paquete.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import database, models
from main import get_password_hash

USERNAME = "googleplayreview"
EMAIL = "google-play-review@atlasflags.invalid"


def main() -> None:
    password = getpass("Contraseña para la cuenta de revisión de Google Play: ")
    if len(password.encode("utf-8")) < 8 or len(password.encode("utf-8")) > 72:
        raise SystemExit("La contraseña debe tener entre 8 y 72 bytes UTF-8.")

    db = database.SessionLocal()
    try:
        existing = db.query(models.User).filter(
            (models.User.username == USERNAME) | (models.User.email == EMAIL)
        ).first()
        if existing:
            if existing.username != USERNAME or existing.email != EMAIL:
                raise SystemExit("Ya existe una cuenta que ocupa el alias o correo de revisión.")
            print(f"La cuenta ya existe. RevenueCat App User ID: atlasflags-user-{existing.id}")
            return

        reviewer = models.User(
            username=USERNAME,
            email=EMAIL,
            full_name="Google Play reviewer",
            hashed_password=get_password_hash(password),
            is_active=True,
            is_verified=True,
        )
        db.add(reviewer)
        db.commit()
        db.refresh(reviewer)
        print(f"Cuenta creada. RevenueCat App User ID: atlasflags-user-{reviewer.id}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
