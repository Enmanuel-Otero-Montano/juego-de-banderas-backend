import os
from types import SimpleNamespace

os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-at-least-32-characters"
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ALLOWED_ORIGINS"] = '["http://testserver"]'
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
os.environ["REFRESH_TOKEN_EXPIRE_DAYS"] = "30"

import pytest
import bcrypt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import CareerAttempt, CareerSeasonProfile, StageBest, StageRun, User
from dependencies import get_current_active_user, get_db
from routers.career import router
from routers.users import user_router
from schemas.daily_challenge_schema import GuessRequest
from utils.career_scoring import calculate_score
from utils.career_rules import STAGE_COUNTRY_CODES
from utils.auth_rate_limit import consume_auth_attempt
from utils.client_ip import get_client_ip
from utils.country_regions import COUNTRY_REGION
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
backend_main.app.dependency_overrides[backend_main.get_current_active_user] = override_current_user
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


def create_attempt(stage_id: int, difficulty: str, route_position: int | None = None) -> dict:
    response = client.post(
        "/career/attempts",
        json={
            "route_position": route_position or stage_id,
            "content_stage_id": stage_id,
            "difficulty": difficulty,
            "season_id": "season-1",
            "ruleset_version": 4,
            "content_version": 1,
            "app_version": "1.0.0-test",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def record_selection(attempt: dict, sequence: int, country_code: str, selected_code: str, event_id: str | None = None):
    return client.post(
        f"/career/attempts/{attempt['attempt_id']}/events",
        json={
            "event_id": event_id or f"event-{sequence:012d}-test",
            "sequence": sequence,
            "country_code": country_code,
            "selected_code": selected_code,
        },
    )


def complete_attempt(attempt: dict, correct_count: int | None = None):
    codes = attempt["country_codes"]
    correct_count = len(codes) if correct_count is None else correct_count
    for index, code in enumerate(codes[:correct_count], start=1):
        response = record_selection(attempt, index, code, code)
        assert response.status_code == 201, response.text
    return client.post(f"/career/attempts/{attempt['attempt_id']}/complete", json={"score": 9999, "time_seconds": 0})


def test_scoring_is_authoritative_and_explainable():
    result = calculate_score(answers(list(STAGE_COUNTRY_CODES[1])[:10]), time_seconds=35, difficulty="normal")
    assert result == {
        "score": 93,
        "base_score": 87,
        "time_bonus": 6,
        "clean_bonus": 0,
        "hints_used": 1,
        "mistakes": 1,
    }


def test_incomplete_stage_does_not_receive_completion_bonuses():
    result = calculate_score(
        answers(list(STAGE_COUNTRY_CODES[1])[:10], correct_count=9),
        time_seconds=5,
        difficulty="normal",
    )
    assert result["time_bonus"] == 0
    assert result["clean_bonus"] == 0


def test_profile_stage_and_leaderboard_contract():
    profile = client.put(
        "/career/profile",
        json={"display_name": "Capitana Atlas", "country": "uy"},
    )
    assert profile.status_code == 200
    assert profile.json()["ranked_profile_ready"] is True
    assert profile.json()["country"] == "UY"
    assert profile.json()["region"] == "Americas"

    completed = complete_attempt(create_attempt(1, "normal"))
    assert completed.status_code == 200
    assert completed.json()["stage_best"]["score"] == completed.json()["score"]
    assert completed.json()["base_score"] == 100
    assert completed.json()["stage_best"]["hints_used"] == 0
    assert completed.json()["stage_best"]["mistakes"] == 0

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
        "total_score": completed.json()["score"],
        "total_hints_used": 0,
        "total_mistakes": 0,
    }


def test_difficulties_are_ranked_separately():
    normal = complete_attempt(create_attempt(1, "normal"))
    easy = complete_attempt(create_attempt(1, "easy"))
    assert normal.status_code == easy.status_code == 200

    normal_board = client.get("/career/leaderboard?difficulty=normal").json()
    easy_board = client.get("/career/leaderboard?difficulty=easy").json()
    assert normal_board["items"][0]["total_score"] == normal.json()["score"]
    assert easy_board["items"][0]["total_score"] == easy.json()["score"]
    assert normal_board["items"][0]["difficulty"] == "normal"
    assert easy_board["items"][0]["difficulty"] == "easy"

    normal_profile = client.get("/career/me?difficulty=normal").json()
    easy_profile = client.get("/career/me?difficulty=easy").json()
    assert normal_profile["total_score"] == normal.json()["score"]
    assert easy_profile["total_score"] == easy.json()["score"]
    assert "career_user_stats" not in Base.metadata.tables


def test_server_chooses_the_plan_and_rejects_a_client_supplied_subset():
    response = client.post(
        "/career/attempts",
        json={
            "route_position": 1,
            "content_stage_id": 1,
            "difficulty": "easy",
            "country_codes": ["uy"] * 8,
            "season_id": "season-1",
            "ruleset_version": 4,
            "content_version": 1,
        },
    )
    assert response.status_code == 422

    plan = create_attempt(1, "easy")
    assert plan["country_codes"] == list(STAGE_COUNTRY_CODES[1])[:8]


def test_events_are_idempotent_sequenced_and_bound_to_the_plan():
    attempt = create_attempt(1, "easy")
    first_code = attempt["country_codes"][0]
    first = record_selection(attempt, 1, first_code, first_code, "event-000000000001-test")
    duplicate = record_selection(attempt, 1, first_code, first_code, "event-000000000001-test")
    assert first.status_code == duplicate.status_code == 201
    assert duplicate.json()["sequence"] == 1

    skipped = record_selection(attempt, 3, attempt["country_codes"][1], attempt["country_codes"][1])
    assert skipped.status_code == 409
    assert "next sequence" in skipped.json()["detail"]

    outside = record_selection(attempt, 2, "zz", first_code)
    assert outside.status_code == 422
    assert "server-issued plan" in outside.json()["detail"]


def test_finalization_uses_persisted_events_not_a_client_summary():
    attempt = create_attempt(1, "normal")
    response = complete_attempt(attempt, correct_count=6)
    assert response.status_code == 200
    assert response.json()["correct_answers"] == 6
    assert response.json()["ranked"] is False
    assert response.json()["stage_best"] is None


def test_legacy_final_payload_is_rejected():
    response = client.post("/career/stages/1/complete", json={})
    assert response.status_code == 426
    assert response.json()["detail"] == "Ranking protocol upgrade required"


def test_ranked_progression_cannot_skip_route_positions():
    response = client.post(
        "/career/attempts",
        json={
            "route_position": 2,
            "content_stage_id": 2,
            "difficulty": "easy",
            "season_id": "season-1",
            "ruleset_version": 4,
            "content_version": 1,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "Complete the previous route stage before ranking this one"


def test_failed_run_is_audited_but_never_counted_as_completed():
    response = complete_attempt(create_attempt(1, "normal"), correct_count=6)
    assert response.status_code == 200
    assert response.json()["ranked"] is False
    assert response.json()["stage_best"] is None
    assert client.get("/career/leaderboard?difficulty=normal").json()["total"] == 0
    with TestingSessionLocal() as db:
        run = db.query(StageRun).one()
        assert run.passed is False
        assert run.correct_answers == 6


def test_only_a_fully_resolved_stage_is_ranked():
    response = complete_attempt(create_attempt(1, "normal"), correct_count=9)
    assert response.status_code == 200
    assert response.json()["correct_answers"] == 9
    assert response.json()["ranked"] is False
    assert response.json()["stage_best"] is None


def test_history_returns_ranked_and_incomplete_attempts():
    failed = complete_attempt(create_attempt(1, "normal"), correct_count=9)
    passed = complete_attempt(create_attempt(1, "normal"))
    assert failed.status_code == passed.status_code == 200

    response = client.get("/career/me/history?difficulty=normal&limit=10")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert [item["passed"] for item in payload["items"]] == [True, False]
    assert all(item["flags_total"] == 10 for item in payload["items"])


def test_completion_is_idempotent_and_server_time_is_authoritative():
    attempt = create_attempt(1, "normal")
    first = complete_attempt(attempt)
    second = client.post(f"/career/attempts/{attempt['attempt_id']}/complete")
    assert first.status_code == 200
    assert first.json()["stage_best"]["time_seconds"] <= 95
    assert second.status_code == 200
    assert second.json()["stage_run_id"] == first.json()["stage_run_id"]


def test_origin_is_locked_after_joining_the_active_season():
    create_attempt(1, "easy")
    response = client.put(
        "/career/profile",
        json={"country": "BR"},
    )
    assert response.status_code == 409
    assert "locked" in response.json()["detail"]


def test_profile_rejects_country_region_mismatch():
    response = client.put(
        "/career/profile",
        json={"country": "UY", "region": "Europe"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ranking region does not match country"


def test_profile_rejects_unknown_country_code():
    response = client.put(
        "/career/profile",
        json={"country": "ZZ"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "country must be a supported ISO alpha-2 code"


def test_profile_does_not_allow_region_without_country():
    response = client.put(
        "/career/profile",
        json={"region": "Europe"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "Ranking region is derived from country"


def test_legacy_matching_region_is_accepted_but_server_remains_authoritative():
    response = client.put(
        "/career/profile",
        json={"country": "es", "region": "Europe"},
    )
    assert response.status_code == 200
    assert response.json()["country"] == "ES"
    assert response.json()["region"] == "Europe"


def test_country_catalog_covers_all_playable_countries():
    assert len(COUNTRY_REGION) == 195
    assert COUNTRY_REGION["UY"] == "Americas"
    assert COUNTRY_REGION["ES"] == "Europe"


def test_region_filter_uses_country_as_authority_for_existing_rows():
    with TestingSessionLocal() as db:
        db.add(
            CareerSeasonProfile(
                user_id=1,
                season_id="season-1",
                country="UY",
                region="Europe",
            )
        )
        db.add(
            StageBest(
                user_id=1,
                stage_id="1",
                route_position=1,
                season_id="season-1",
                ruleset_version=4,
                content_version=1,
                score=100,
                mistakes=0,
                difficulty="normal",
                hints_used=0,
                time_seconds=30,
            )
        )
        db.commit()

    americas = client.get("/career/leaderboard?difficulty=normal&region=Americas").json()
    europe = client.get("/career/leaderboard?difficulty=normal&region=Europe").json()

    assert americas["total"] == 1
    assert americas["items"][0]["region"] == "Americas"
    assert europe["total"] == 0


def test_account_deletion_removes_profile_and_ranked_results():
    completed = complete_attempt(create_attempt(1, "normal"))
    assert completed.status_code == 200

    deleted = client.delete("/users/me")
    assert deleted.status_code == 204
    with TestingSessionLocal() as db:
        assert db.query(User).filter(User.id == 1).first() is None

    leaderboard = client.get("/career/leaderboard?difficulty=normal").json()
    assert leaderboard["total"] == 0


def test_legacy_profile_route_is_scoped_to_the_authenticated_user():
    own_profile = registration_client.get("/user-profile/1")
    assert own_profile.status_code == 200
    assert own_profile.json()["username"] == "atlas"

    response = registration_client.get("/user-profile/2")
    assert response.status_code == 403
    assert response.json()["message"] == "You can only read your own profile"


def test_legacy_avatar_route_redirects_to_the_canonical_endpoint():
    response = client.get("/users/1/profile-image", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/user/1/profile_image"


def test_api_documentation_is_disabled_in_production():
    assert backend_main.api_documentation_urls("production") == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
    assert backend_main.api_documentation_urls("test")["docs_url"] == "/docs"


def test_public_privacy_and_account_deletion_pages_are_available():
    privacy = registration_client.get("/privacy.html")
    deletion = registration_client.get("/delete-account.html")

    assert privacy.status_code == 200
    assert "Política de privacidad" in privacy.text
    assert deletion.status_code == 200
    assert "Eliminar una cuenta" in deletion.text


def test_daily_guess_is_trimmed_and_bounded_before_persistence():
    assert GuessRequest(guess="  Uruguay ").guess == "Uruguay"
    with pytest.raises(ValueError):
        GuessRequest(guess="x" * 121)


def test_legacy_daily_challenge_is_explicitly_retired():
    response = registration_client.get("/daily-challenge/today")

    assert response.status_code == 410
    assert response.json()["message"] == "The legacy daily challenge was retired; use the mobile daily challenge"


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


def test_refresh_tokens_rotate_and_reuse_revokes_their_family():
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.id == 1).one()
        user.hashed_password = backend_main.get_password_hash("initial-password")
        db.commit()
    issued = registration_client.post("/token", data={"username": "atlas", "password": "initial-password"})
    assert issued.status_code == 200
    first = issued.json()
    assert first["expires_in"] == 1800
    assert first["refresh_token"]

    rotated = registration_client.post("/token/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    reused = registration_client.post("/token/refresh", json={"refresh_token": first["refresh_token"]})
    assert reused.status_code == 401
    revoked_family = registration_client.post("/token/refresh", json={"refresh_token": second["refresh_token"]})
    assert revoked_family.status_code == 401

    with TestingSessionLocal() as db:
        sessions = db.query(backend_main.models.AuthRefreshSession).filter(backend_main.models.AuthRefreshSession.user_id == 1).all()
        assert len(sessions) == 2
        assert all(session.token_hash != first["refresh_token"] for session in sessions)


def test_verification_resend_does_not_disclose_account_existence():
    known = registration_client.post("/resend-verification-email", json="atlas@example.com")
    unknown = registration_client.post("/resend-verification-email", json="missing@example.com")

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {
        "msg": "If the account requires verification, an email was sent"
    }


def test_password_reset_does_not_disclose_account_existence_and_invalidates_used_link():
    page = registration_client.get("/reset-password")
    known = registration_client.post("/password-reset/request", json="atlas@example.com")
    unknown = registration_client.post("/password-reset/request", json="missing@example.com")

    assert page.status_code == 200
    assert "Restablecer contraseña" in page.text
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {
        "msg": "If the account exists, a password reset email was sent"
    }

    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.id == 1).one()
        user.hashed_password = backend_main.get_password_hash("initial-password")
        db.commit()
    issued = registration_client.post("/token", data={"username": "atlas", "password": "initial-password"})
    assert issued.status_code == 200
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.email == "atlas@example.com").one()
        token = backend_main.create_password_reset_token(user.email, user.hashed_password)

    changed = registration_client.post("/password-reset/confirm", json={"token": token, "password": "new-secure-password"})
    reused = registration_client.post("/password-reset/confirm", json={"token": token, "password": "another-password"})

    assert changed.status_code == 200
    assert reused.status_code == 400
    assert registration_client.post("/token/refresh", json={"refresh_token": issued.json()["refresh_token"]}).status_code == 401
    assert registration_client.post("/token", data={"username": "atlas", "password": "new-secure-password"}).status_code == 200


def test_shared_auth_limit_rejects_attempts_over_the_window_limit():
    with TestingSessionLocal() as db:
        consume_auth_attempt(db, scope="login", subject="atlas@example.com", client_ip="127.0.0.1", maximum=2, window_seconds=60)
        consume_auth_attempt(db, scope="login", subject="atlas@example.com", client_ip="127.0.0.1", maximum=2, window_seconds=60)
        with pytest.raises(backend_main.HTTPException) as error:
            consume_auth_attempt(db, scope="login", subject="atlas@example.com", client_ip="127.0.0.1", maximum=2, window_seconds=60)
    assert error.value.status_code == 429


def test_client_ip_uses_cloudflare_header_only_from_render_private_proxy():
    render_request = Request({
        "type": "http",
        "headers": [(b"cf-connecting-ip", b"186.55.204.150"), (b"x-forwarded-for", b"203.0.113.17")],
        "client": ("10.238.25.42", 12345),
    })
    direct_request = Request({
        "type": "http",
        "headers": [(b"cf-connecting-ip", b"203.0.113.17")],
        "client": ("8.8.8.8", 12345),
    })

    assert get_client_ip(render_request) == "186.55.204.150"
    assert get_client_ip(direct_request) == "8.8.8.8"
