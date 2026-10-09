"""Optional real-lock tests. RACE_TEST_DATABASE_URL must point to a disposable
PostgreSQL database whose name contains 'test'. Never uses application DATABASE_URL.
Creates and drops only a uniquely named schema inside that database.
"""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-with-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite://")
os.environ.setdefault("ALLOWED_ORIGINS", '["http://testserver"]')

from db.database import Base
from db.models import User, FlagRaceRoom, FlagRaceRoomMember, FlagRaceRound, FlagRaceAnswerEvent
from routers import race_rooms
from schemas.race import RaceAnswerMessage

pytestmark = pytest.mark.skipif(not os.environ.get("RACE_TEST_DATABASE_URL"), reason="RACE_TEST_DATABASE_URL not configured")


@pytest.fixture
def sessions(monkeypatch):
    url = make_url(os.environ["RACE_TEST_DATABASE_URL"])
    assert url.get_backend_name() == "postgresql" and "test" in (url.database or "").lower()
    schema = f"race_sync_{uuid4().hex}"
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000"})
    try:
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine)
        with factory() as db:
            for user_id in (1, 2):
                db.add(User(id=user_id, email=f"{user_id}@example.test", username=f"player{user_id}", hashed_password="unused", is_active=True))
            db.flush()
            db.add(FlagRaceRoom(id="room", join_code_hash="code", join_code_encrypted="unused", join_token_hash="token", join_token_encrypted="unused",
                                owner_user_id=1, current_host_user_id=1, expires_at=race_rooms.utc_now() + timedelta(minutes=30)))
            db.flush()
            for user_id in (1, 2):
                db.add(FlagRaceRoomMember(room_id="room", user_id=user_id, seat=user_id, is_ready=True, is_connected=True))
            db.commit()
        monkeypatch.setattr(race_rooms, "schedule_round_deadline", lambda *args: None)
        yield factory
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_concurrent_host_starts_create_exactly_one_round(sessions):
    barrier = Barrier(2)
    def start():
        with sessions() as db:
            barrier.wait(timeout=5)
            return asyncio.run(race_rooms.start_race_round("room", current_user=SimpleNamespace(id=1), db=db))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results[0]["id"] == results[1]["id"]
    assert results[0]["starts_at"] == results[1]["starts_at"]
    assert results[0]["deadline_at"] == results[1]["deadline_at"]
    with sessions() as db:
        assert db.query(FlagRaceRound).count() == 1
        assert len(db.query(FlagRaceRound).one().participants) == 2


def test_concurrent_final_answers_produce_one_winner(sessions):
    with sessions() as db:
        started = asyncio.run(race_rooms.start_race_round("room", current_user=SimpleNamespace(id=1), db=db))
        race = db.query(FlagRaceRound).one()
        race.starts_at = race_rooms.utc_now() - timedelta(seconds=10)
        race.deadline_at = race_rooms.utc_now() + timedelta(seconds=80)
        for participant in race.participants:
            participant.progress = 11
        question = race.plan[-1]
        db.commit()
    barrier = Barrier(2)
    def answer(user_id):
        with sessions() as db:
            barrier.wait(timeout=5)
            # Same lock order as the WebSocket handler: room, then round.
            room = db.query(FlagRaceRoom).filter_by(id="room").with_for_update().one()
            user = db.query(User).filter_by(id=user_id).one()
            message = RaceAnswerMessage(round_id=started["id"], event_id=f"final-race-event-{user_id}", sequence=1,
                                        country_code=question["country_code"], selected_code=question["country_code"])
            try:
                return asyncio.run(race_rooms.process_answer(db, room, user, message))
            except HTTPException as error:
                db.rollback()
                return "rejected", error.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(answer, user_id) for user_id in (1, 2)]
        results = [future.result(timeout=15) for future in futures]
    assert sorted(result[0] for result in results) == ["race_finished", "rejected"]
    with sessions() as db:
        race = db.query(FlagRaceRound).one()
        assert race.winner_user_id in (1, 2)
        assert race.status == "finished"
        assert db.query(FlagRaceAnswerEvent).count() == 1
