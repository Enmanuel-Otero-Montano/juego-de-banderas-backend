from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config, pool
from alembic import context

# Alembic Config
config = context.config

# Permitir override de la URL vía env var (p.ej. CI/Docker)
env_url = os.getenv("DATABASE_URL")
if env_url:
    config.set_main_option("sqlalchemy.url", env_url)

# Logging desde alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# === Importá tu Base y modelos para poblar target_metadata ===
from db.database import Base  # noqa: E402
# Asegurate que este import registre TODOS los modelos en Base.metadata:
# si db/models/__init__.py ya importa los submódulos, alcanza con esta línea.
from db import models as _models  # noqa: F401,E402

target_metadata = Base.metadata


def include_object(object, name, type_, reflected, compare_to):
    """Excluir objetos que no querés versionar (p.ej. tabla de control de Alembic)."""
    if type_ == "table" and name == "alembic_version":
        return False
    return True


def run_migrations_offline() -> None:
    """Modo 'offline': genera SQL sin conectarse al motor."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # Mejores diffs/autogenerate
        compare_type=True,
        compare_server_default=True,
        # Soporte de schemas (PostgreSQL)
        include_schemas=True,
        # Tabla de control explícita
        version_table="alembic_version",
        version_table_schema="public",
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Modo 'online': aplica migraciones contra una conexión real."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Mejores diffs/autogenerate
            compare_type=True,
            compare_server_default=True,
            # Soporte de schemas (PostgreSQL)
            include_schemas=True,
            # Tabla de control explícita
            version_table="alembic_version",
            version_table_schema="public",
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
