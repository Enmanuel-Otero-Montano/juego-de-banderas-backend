"""Concurrencia del ranking v4 contra PostgreSQL real.

``SELECT ... FOR UPDATE`` sobre ``CareerAttempt`` serializa dos selecciones
del mismo intento. SQLite en memoria no toma ese bloqueo, así que la suite
de ``tests/test_career_api.py`` no puede demostrar que un ``event_id``
duplicado o la secuencia N+1 dejen una sola fila. Este módulo usa
conexiones reales y no toca el ``DATABASE_URL`` fijado a SQLite.

Cómo levantarlo:

    docker run --rm -d --name ranking-v4-concurrency-pg \
      -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=ranking_v4_concurrency_test \
      -p 55432:5432 postgres:16

    export RANKING_TEST_DATABASE_URL='postgresql+psycopg2://postgres:postgres@127.0.0.1:55432/ranking_v4_concurrency_test'
    pytest tests/test_career_ranking_postgres_concurrency.py -q

También sirve ``TEST_DATABASE_URL``. El esquema tiene que ser PostgreSQL
(``postgresql`` o ``postgresql+psycopg2``). El nombre de la base tiene que
contener ``test``. Si la variable no está o el servidor no responde, el
módulo se salta. Si la URL está pero no parece de test, se aborta: estas
pruebas crean y destruyen el schema ``ranking_v4_concurrency_test`` y no
deben caer sobre datos ajenos.

El intervalo mínimo de 0,5 s entre selecciones queda fuera de estas pruebas
porque se desactiva con ``ENV=test``, igual que en la suite SQLite. El TTL
del intento sigue vigente.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

SCHEMA = "ranking_v4_concurrency_test"
_SAFE_SCHEMA = SCHEMA.isidentifier()


pytestmark = pytest.mark.skipif(
    not os.environ.get("RANKING_TEST_DATABASE_URL", "").strip()
    and not os.environ.get("TEST_DATABASE_URL", "").strip(),
    reason=(
        "PostgreSQL de test no configurado. Definí RANKING_TEST_DATABASE_URL "
        "o TEST_DATABASE_URL (esquema postgresql, base cuyo nombre contenga "
        "'test'). Ver el docstring de este módulo. No se usa DATABASE_URL."
    ),
)


def _ranking_test_database_url() -> str:
    """Lee sólo variables de test. ``DATABASE_URL`` de la app queda aparte."""
    for name in ("RANKING_TEST_DATABASE_URL", "TEST_DATABASE_URL"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    pytest.skip("Falta RANKING_TEST_DATABASE_URL o TEST_DATABASE_URL")


def _require_disposable_postgres_url(raw: str) -> str:
    if raw.startswith("postgres://"):
        raw = raw.replace("postgres://", "postgresql+psycopg2://", 1)
    try:
        parsed = make_url(raw)
    except Exception as error:
        pytest.fail(f"La URL de concurrencia no se puede interpretar: {error}")
    if not parsed.drivername.startswith("postgresql"):
        pytest.fail(
            f"La URL de concurrencia usa {parsed.drivername}. "
            "Hace falta PostgreSQL. No se usa DATABASE_URL."
        )
    database = (parsed.database or "").lower()
    if "test" not in database:
        pytest.fail(
            "Se aborta: el nombre de la base no contiene 'test'. "
            "Estas pruebas crean y destruyen un schema y no deben apuntar a datos ajenos."
        )
    return raw


def _load_ranking_api():
    """Importa el router sin reabrir la base de producción.

    Si ``config`` todavía no se cargó, este módulo corre solo. El engine
    global de la app se apunta a SQLite en memoria y no se usa. Si la suite
    SQLite ya importó la app, no se pisa el ``DATABASE_URL`` que ella fijó.
    """
    if "config" not in sys.modules:
        os.environ["ENV"] = "test"
        os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-characters")
        os.environ.setdefault("ALLOWED_ORIGINS", '["http://testserver"]')
        os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
        os.environ.setdefault("REFRESH_TOKEN_EXPIRE_DAYS", "30")
        os.environ["DATABASE_URL"] = "sqlite+pysqlite://"

    from config import settings
    from db.database import Base
    from db.models import CareerAttemptEvent, User
    from routers.career import create_ranked_attempt, record_ranked_selection
    from schemas.score import CareerAttemptCreate, RankedSelectionEvent

    if settings.ENV != "test":
        pytest.fail(
            "Estas pruebas necesitan ENV=test para no aplicar el intervalo de 0,5 s entre selecciones."
        )
    return {
        "Base": Base,
        "User": User,
        "CareerAttemptEvent": CareerAttemptEvent,
        "create_ranked_attempt": create_ranked_attempt,
        "record_ranked_selection": record_ranked_selection,
        "CareerAttemptCreate": CareerAttemptCreate,
        "RankedSelectionEvent": RankedSelectionEvent,
    }


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/career/attempts/concurrency/events",
            "raw_path": b"/career/attempts/concurrency/events",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
        }
    )


def _describe(result) -> str:
    if isinstance(result, HTTPException):
        return f"HTTP {result.status_code}: {result.detail}"
    if isinstance(result, dict):
        return f"accepted sequence={result.get('sequence')} event_id={result.get('event_id')}"
    return f"{type(result).__name__}: {result}"


@pytest.fixture(scope="module")
def ranking_api():
    return _load_ranking_api()


@pytest.fixture(scope="module")
def postgres_engine(ranking_api):
    if not _SAFE_SCHEMA:
        pytest.fail("El schema de concurrencia tiene un identificador inseguro")
    url = _require_disposable_postgres_url(_ranking_test_database_url())
    pytest.importorskip("psycopg2")
    from sqlalchemy import create_engine, event, text
    base_engine = create_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        connect_args={"connect_timeout": 3},
    )

    @event.listens_for(base_engine, "connect")
    def _isolate_connection(dbapi_connection, _record):
        # Fuera de una transacción: un ROLLBACK del pool no debe devolver
        # el search_path a public y hacer que el ORM toque tablas ajenas.
        previous_autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        cursor = dbapi_connection.cursor()
        cursor.execute(f"SET search_path TO {SCHEMA}")
        cursor.execute("SET lock_timeout = '5s'")
        cursor.close()
        dbapi_connection.autocommit = previous_autocommit

    engine = base_engine.execution_options(schema_translate_map={None: SCHEMA})

    # Sólo se borra este schema, y sólo después de comprobar que la base es de test.
    safe_to_drop = False
    try:
        try:
            with engine.connect() as connection:
                current_database = connection.execute(text("SELECT current_database()")).scalar()
                if not current_database or "test" not in current_database.lower():
                    pytest.fail(
                        "Se aborta: current_database() no contiene 'test'. "
                        "No se crean ni se borran objetos."
                    )
                isolation = connection.execute(text("SHOW transaction_isolation")).scalar()
                if isolation != "read committed":
                    pytest.fail(f"Se esperaba read committed y la sesión está en {isolation}")
                public_tables = connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name IN (
                            'users', 'career_attempts', 'career_attempt_events',
                            'stage_runs', 'stage_best'
                          )
                        """
                    )
                ).scalars().all()
                if public_tables:
                    pytest.fail(
                        "Se aborta: public ya tiene tablas de ranking "
                        f"({', '.join(public_tables)}). No se modifican."
                    )
                safe_to_drop = True
                connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
                connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
                connection.commit()
        except OperationalError as error:
            message = str(error).lower()
            unreachable = (
                "could not connect" in message
                or "connection refused" in message
                or "timeout" in message
                or "could not translate" in message
                or "name or service not known" in message
            )
            if unreachable:
                pytest.skip(f"PostgreSQL de test no alcanzable: {error.__class__.__name__}")
            raise

        ranking_api["Base"].metadata.create_all(bind=engine)
        with engine.connect() as connection:
            schemas = connection.execute(
                text(
                    """
                    SELECT table_schema
                    FROM information_schema.tables
                    WHERE table_name = 'career_attempt_events'
                    """
                )
            ).scalars().all()
            if schemas != [SCHEMA]:
                pytest.fail(
                    "career_attempt_events no quedó sólo en el schema de test "
                    f"(schemas={schemas}). No se continúa."
                )

        yield engine
    finally:
        if safe_to_drop:
            with engine.begin() as connection:
                connection.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        engine.dispose()


@pytest.fixture
def session_factory(postgres_engine):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                TRUNCATE TABLE
                    {SCHEMA}.career_attempt_events,
                    {SCHEMA}.stage_runs,
                    {SCHEMA}.stage_best,
                    {SCHEMA}.career_attempts,
                    {SCHEMA}.career_season_profiles,
                    {SCHEMA}.auth_refresh_sessions,
                    {SCHEMA}.users
                RESTART IDENTITY CASCADE
                """
            )
        )
    return sessionmaker(autocommit=False, autoflush=False, bind=postgres_engine)


def _open_attempt(session_factory, ranking_api) -> tuple[str, list[str]]:
    """Mismo contrato que ``create_attempt`` de la suite SQLite: perfil UY y etapa 1."""
    User = ranking_api["User"]
    session = session_factory()
    try:
        session.add(
            User(
                id=1,
                email="atlas@example.com",
                username="atlas",
                hashed_password="unused",
                is_active=True,
                is_verified=True,
                country="UY",
                ranking_region="Americas",
            )
        )
        session.commit()
        created = asyncio.run(
            ranking_api["create_ranked_attempt"](
                request=_request(),
                payload=ranking_api["CareerAttemptCreate"](
                    route_position=1,
                    content_stage_id=1,
                    difficulty="easy",
                    season_id="season-1",
                    ruleset_version=4,
                    content_version=1,
                    app_version="1.0.0-test",
                ),
                current_user=SimpleNamespace(id=1, is_active=True, email="atlas@example.com"),
                db=session,
            )
        )
    finally:
        session.close()
    return created["attempt_id"], created["country_codes"]


def _record(session_factory, ranking_api, attempt_id: str, payload, barrier: threading.Barrier | None):
    session = session_factory()
    try:
        if barrier is not None:
            barrier.wait(timeout=10)
        return asyncio.run(
            ranking_api["record_ranked_selection"](
                request=_request(),
                attempt_id=attempt_id,
                payload=payload,
                current_user=SimpleNamespace(id=1, is_active=True, email="atlas@example.com"),
                db=session,
            )
        )
    except HTTPException as error:
        session.rollback()
        return error
    finally:
        session.close()


def _run_concurrent(session_factory, ranking_api, attempt_id: str, payloads: list):
    barrier = threading.Barrier(len(payloads))
    results: list = [None] * len(payloads)

    def _worker(index: int, payload) -> None:
        try:
            results[index] = _record(session_factory, ranking_api, attempt_id, payload, barrier)
        except Exception as error:
            results[index] = error

    threads = [
        threading.Thread(target=_worker, args=(index, payload), name=f"ranked-event-{index}")
        for index, payload in enumerate(payloads)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive(), "la transacción concurrente no terminó; el bloqueo no se soltó"
    return results


def _stored_events(session_factory, ranking_api, attempt_id: str) -> list[tuple]:
    CareerAttemptEvent = ranking_api["CareerAttemptEvent"]
    session = session_factory()
    try:
        rows = (
            session.query(CareerAttemptEvent)
            .filter(CareerAttemptEvent.attempt_id == attempt_id)
            .order_by(CareerAttemptEvent.sequence)
            .all()
        )
        return [(row.event_id, row.sequence, row.country_code, row.selected_code) for row in rows]
    finally:
        session.close()


def _event(ranking_api, event_id: str, sequence: int, country_code: str, selected_code: str):
    return ranking_api["RankedSelectionEvent"](
        event_id=event_id,
        sequence=sequence,
        country_code=country_code,
        selected_code=selected_code,
    )


def test_concurrent_same_event_id_does_not_duplicate_the_row(session_factory, ranking_api):
    attempt_id, codes = _open_attempt(session_factory, ranking_api)
    country_code = codes[0]
    payload = _event(ranking_api, "event-same-id-0001", 1, country_code, country_code)

    results = _run_concurrent(session_factory, ranking_api, attempt_id, [payload, payload])
    stored = _stored_events(session_factory, ranking_api, attempt_id)

    assert len(stored) == 1, results
    assert stored[0][0] == "event-same-id-0001"
    assert stored[0][1] == 1
    for result in results:
        if isinstance(result, dict):
            assert result["event_id"] == "event-same-id-0001"
            assert result["sequence"] == 1
            continue
        assert isinstance(result, HTTPException), _describe(result)
        assert result.status_code == 409
        assert result.detail == "ranked event was already recorded"
    assert any(isinstance(result, dict) for result in results), [_describe(result) for result in results]


def test_concurrent_next_sequence_keeps_a_single_successor(session_factory, ranking_api):
    attempt_id, codes = _open_attempt(session_factory, ranking_api)
    country_code = codes[0]
    payloads = [
        _event(ranking_api, "event-seq-racing-001", 1, country_code, country_code),
        _event(ranking_api, "event-seq-racing-002", 1, country_code, codes[1]),
    ]

    results = _run_concurrent(session_factory, ranking_api, attempt_id, payloads)
    stored = _stored_events(session_factory, ranking_api, attempt_id)
    accepted = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, HTTPException)]
    described = [_describe(result) for result in results]

    assert len(stored) == 1, described
    assert len(accepted) == 1, described
    assert len(conflicts) == 1, described
    assert conflicts[0].status_code == 409
    # El perdedor espera el FOR UPDATE y relee la secuencia ya commiteada.
    # "already recorded" sería el índice único, no el bloqueo de la fila padre.
    assert conflicts[0].detail == "ranked events must use the next sequence number"
    winner = next(payload for payload in payloads if payload.event_id == accepted[0]["event_id"])
    assert accepted[0]["sequence"] == 1
    assert stored == [(winner.event_id, 1, country_code, winner.selected_code)]


def test_identical_retry_after_commit_does_not_duplicate_the_event(session_factory, ranking_api):
    attempt_id, codes = _open_attempt(session_factory, ranking_api)
    country_code = codes[0]
    payload = _event(ranking_api, "event-retry-commit1", 1, country_code, country_code)

    first = _record(session_factory, ranking_api, attempt_id, payload, barrier=None)
    second = _record(session_factory, ranking_api, attempt_id, payload, barrier=None)
    stored = _stored_events(session_factory, ranking_api, attempt_id)

    assert isinstance(first, dict), _describe(first)
    assert isinstance(second, dict), _describe(second)
    assert first["event_id"] == second["event_id"] == "event-retry-commit1"
    assert first["sequence"] == second["sequence"] == 1
    assert first["accepted_at"] == second["accepted_at"]
    assert stored == [("event-retry-commit1", 1, country_code, country_code)]
