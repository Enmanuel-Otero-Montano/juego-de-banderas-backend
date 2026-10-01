#!/bin/sh
set -eu

alembic upgrade head
python scripts/provision_staging_players.py

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
