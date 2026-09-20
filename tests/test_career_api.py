import os
from types import SimpleNamespace

os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-at-least-32-characters"
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ALLOWED_ORIGINS"] = '["http://testserver"]'

import pytest
import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import CareerAttempt, StageRun, User
from dependencies import get_current_active_user, get_db
from routers.career import router
from routers.users import user_router
from utils.career_scoring import calculate_score
from utils.career_rules import STAGE_COUNTRY_CODES
import main as backend_main


engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()
app.include_router(router)
app.include_router(user_router)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


async def override_current_user():
    return SimpleNamespace(id=1, is_active=True, email="atlas@example.com")


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_active_user] = override_current_user
client = TestClient(app)
backend_main.app.dependency_overrides[backend_main.get_db] = override_get_db
registration_client = TestClient(backend_main.app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestingSessionLocal() as db:
        db.add(
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
        db.commit()
    yield


def answers(codes: list[str], correct_count: int | None = None):
    correct_count = len(codes) if correct_count is None else correct_count
    return [
        {
            "country_code": code,
            "selected_codes": (
                ([codes[(index + 1) % len(codes)], code] if index == 1 else [code])
                if index < correct_count else []
            ),
            "correct": index < correct_count,
            "used_hint": index == 0,
            "wrong_attempts": 1 if index == 1 else 0,
        }
        for index, code in enumerate(codes)
    ]


def create_attempt(stage_id: int, difficulty: str, codes: list[str], route_position: int | None = None):
    response = client.post(
        "/career/attempts",
        json={
            "route_position": route_position or stage_id,
            "content_stage_id": stage_id,
            "difficulty": difficulty,
            "country_codes": codes,
            "season_id": "season-1",
            "ruleset_version": 1,
            "content_version": 1,
            "app_version": "1.0.0-test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["attempt_id"]


def stage_payload(stage_id: str, difficulty: str, codes: list[str], time_seconds: int, correct_count: int | None = None):
    attempt_id = create_attempt(int(stage_id), difficulty, codes)
    return {
        "attempt_id": attempt_id,
        "stage_id": stage_id,
        "route_position": int(stage_id),
        "season_id": "season-1",
        "ruleset_version": 1,
        "content_version": 1,
        "game_mode": "career",
        "difficulty": difficulty,
        "time_seconds": time_seconds,
        "score": 9999,
        "answers": answers(codes, correct_count),
    }


def test_scoring_is_authoritative_and_explainable():
    result = calculate_score(answers(list(STAGE_COUNTRY_CODES[1])[:10]), time_seconds=35, difficulty="normal")
    assert result == {
        "score": 91,
        "base_score": 87,
        "time_bonus": 4,
        "clean_bonus": 0,
        "hints_used": 1,
        "mistakes": 1,
    }


def test_profile_stage_and_leaderboard_contract():
    profile = client.put(
        "/career/profile",
        json={"display_name": "Capitana Atlas", "country": "uy", "region": "Americas"},
    )
    assert profile.status_code == 200
    assert profile.json()["ranked_profile_ready"] is True

    completed = client.post(
        "/career/stages/1/complete",
        json=stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35),
    )
    assert completed.status_code == 200
    assert completed.json()["stage_best"]["score"] == 92
    assert completed.json()["stage_best"]["hints_used"] == 1
    assert completed.json()["stage_best"]["mistakes"] == 1

    leaderboard = client.get("/career/leaderboard?difficulty=normal")
    assert leaderboard.status_code == 200
    payload = leaderboard.json()
    assert payload["total"] == 1
    assert payload["items"][0] == {
        **payload["items"][0],
        "rank": 1,
        "user_id": 1,
        "username": "atlas",
        "display_name": "Capitana Atlas",
        "country": "UY",
        "region": "Americas",
        "difficulty": "normal",
        "stages_completed": 1,
        "total_score": 92,
        "total_hints_used": 1,
        "total_mistakes": 1,
    }


def test_difficulties_are_ranked_separately():
    normal = client.post(
        "/career/stages/1/complete",
        json=stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35),
    )
    easy = client.post(
        "/career/stages/1/complete",
        json=stage_payload("1", "easy", list(STAGE_COUNTRY_CODES[1])[:8], 40),
    )
    assert normal.status_code == easy.status_code == 200

    normal_board = client.get("/career/leaderboard?difficulty=normal").json()
    easy_board = client.get("/career/leaderboard?difficulty=easy").json()
    assert normal_board["items"][0]["total_score"] == 92
    assert easy_board["items"][0]["total_score"] == 72
    assert normal_board["items"][0]["difficulty"] == "normal"
    assert easy_board["items"][0]["difficulty"] == "easy"


@pytest.mark.parametrize(
    ("payload", "detail"),
    [
        (
            lambda: stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35),
            "answers do not match the server-issued attempt",
        ),
    ],
)
def test_invalid_ranked_stage_is_rejected(payload, detail):
    value = payload()
    value["answers"][0]["country_code"] = "zz"
    response = client.post("/career/stages/1/complete", json=value)
    assert response.status_code == 422
    assert response.json()["detail"] == detail


def test_duplicate_country_codes_are_rejected():
    payload = stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35)
    payload["answers"][1]["country_code"] = payload["answers"][0]["country_code"]
    response = client.post("/career/stages/1/complete", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "country codes must be unique within a stage"


def test_arbitrary_stage_ids_are_rejected_before_they_can_affect_ranking():
    payload = stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35)
    payload["stage_id"] = "999"
    response = client.post("/career/stages/999/complete", json=payload)
    assert response.status_code == 422
    assert response.json()["detail"] == "ranked stages must be between 1 and 12"


def test_ranked_progression_cannot_skip_route_positions():
    response = client.post(
        "/career/attempts",
        json={
            "route_position": 2,
            "content_stage_id": 2,
            "difficulty": "easy",
            "country_codes": list(STAGE_COUNTRY_CODES[2])[:8],
            "season_id": "season-1",
            "ruleset_version": 1,
            "content_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Complete the previous route stage before ranking this one"


def test_failed_run_is_audited_but_never_counted_as_completed():
    codes = list(STAGE_COUNTRY_CODES[1])[:10]
    response = client.post(
        "/career/stages/1/complete",
        json=stage_payload("1", "normal", codes, 35, correct_count=6),
    )
    assert response.status_code == 200
    assert response.json()["ranked"] is False
    assert response.json()["stage_best"] is None
    assert client.get("/career/leaderboard?difficulty=normal").json()["total"] == 0
    with TestingSessionLocal() as db:
        run = db.query(StageRun).one()
        assert run.passed is False
        assert run.correct_answers == 6


def test_attempt_is_single_use_and_client_time_is_not_authoritative():
    payload = stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 9999)
    first = client.post("/career/stages/1/complete", json=payload)
    second = client.post("/career/stages/1/complete", json=payload)
    assert first.status_code == 200
    assert first.json()["stage_best"]["time_seconds"] <= 1
    assert second.status_code == 409
    assert second.json()["detail"] == "Ranked attempt was already completed"


def test_origin_is_locked_after_joining_the_active_season():
    create_attempt(1, "easy", list(STAGE_COUNTRY_CODES[1])[:8])
    response = client.put(
        "/career/profile",
        json={"country": "BR", "region": "Americas"},
    )
    assert response.status_code == 409
    assert "locked" in response.json()["detail"]


def test_account_deletion_removes_profile_and_ranked_results():
    completed = client.post(
        "/career/stages/1/complete",
        json=stage_payload("1", "normal", list(STAGE_COUNTRY_CODES[1])[:10], 35),
    )
    assert completed.status_code == 200

    deleted = client.delete("/users/me")
    assert deleted.status_code == 204
    with TestingSessionLocal() as db:
        assert db.query(User).filter(User.id == 1).first() is None

    leaderboard = client.get("/career/leaderboard?difficulty=normal").json()
    assert leaderboard["total"] == 0


def test_registration_validates_credentials_and_normalizes_identity():
    short_password = registration_client.post(
        "/register",
        data={"username": "atlas2", "email": "captain@example.com", "password": "short"},
    )
    assert short_password.status_code == 422

    invalid_email = registration_client.post(
        "/register",
        data={"username": "atlas2", "email": "not-an-email", "password": "secure-pass"},
    )
    assert invalid_email.status_code == 422

    oversized_bcrypt_input = registration_client.post(
        "/register",
        data={"username": "atlas2", "email": "captain@example.com", "password": "🗺️" * 30},
    )
    assert oversized_bcrypt_input.status_code == 422

    created = registration_client.post(
        "/register",
        data={"username": "  atlas2  ", "email": "Captain@Example.COM", "password": "secure-pass"},
    )
    assert created.status_code == 200
    assert created.json()["username"] == "atlas2"
    assert created.json()["email"] == "captain@example.com"
    with TestingSessionLocal() as db:
        created_user = db.query(User).filter(User.username == "atlas2").one()
        assert created_user.hashed_password.startswith("$argon2id$")


def test_successful_login_migrates_legacy_bcrypt_hash_to_argon2():
    legacy_hash = bcrypt.hashpw(b"legacy-pass", bcrypt.gensalt()).decode()
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.id == 1).one()
        user.hashed_password = legacy_hash
        db.commit()

    response = registration_client.post(
        "/token",
        data={"username": "atlas", "password": "legacy-pass"},
    )
    assert response.status_code == 200
    with TestingSessionLocal() as db:
        migrated = db.query(User).filter(User.id == 1).one()
        assert migrated.hashed_password.startswith("$argon2id$")


def test_verification_resend_does_not_disclose_account_existence():
    known = registration_client.post("/resend-verification-email", json="atlas@example.com")
    unknown = registration_client.post("/resend-verification-email", json="missing@example.com")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {
        "msg": "If the account requires verification, an email was sent"
    }
