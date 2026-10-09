import asyncio
import base64
import os
from datetime import timedelta
from types import SimpleNamespace

os.environ["ENV"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-with-at-least-32-characters"
os.environ["DATABASE_URL"] = "sqlite+pysqlite://"
os.environ["ALLOWED_ORIGINS"] = '["http://testserver"]'

import pytest
import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models import FlagRaceAnswerEvent, FlagRaceParticipant, FlagRaceRoom, FlagRaceRoomMember, FlagRaceRound, User
from dependencies import get_current_active_user, get_db
from routers.race_rooms import process_answer, recover_race_state, router, run_race_maintenance_once, settings, utc_now
from schemas.race import RaceAnswerMessage
from utils.limiter import limiter
from utils.race_rules import RACE_FLAGS_TOTAL, build_race_plan, participant_plan


engine = create_engine(
    "sqlite+pysqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
active_user = {"id": 1}

app = FastAPI()
app.include_router(router)
app.state.race_session_factory = TestingSessionLocal


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


async def override_current_user():
    with TestingSessionLocal() as db:
        user = db.query(User).filter(User.id == active_user["id"]).first()
        return SimpleNamespace(id=user.id, is_active=True, email=user.email)


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[get_current_active_user] = override_current_user
client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_database():
    limiter.reset()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestingSessionLocal() as db:
        for user_id in range(1, 10):
            db.add(User(
                id=user_id,
                email=f"player{user_id}@example.com",
                username=f"player{user_id}",
                ranking_alias=f"Player {user_id}",
                hashed_password="unused",
                is_active=True,
                is_verified=True,
            ))
        db.commit()
    active_user["id"] = 1
    yield


def create_and_join_two_players() -> dict:
    active_user["id"] = 1
    created = client.post("/race-rooms", json={"scope": "Americas", "difficulty": "normal"})
    assert created.status_code == 201, created.text
    room = created.json()
    active_user["id"] = 2
    joined = client.post("/race-rooms/join", json={"code": room["code"]})
    assert joined.status_code == 200, joined.text
    return joined.json()


def prepare_round(room_id: str) -> dict:
    with TestingSessionLocal() as db:
        members = db.query(FlagRaceRoomMember).filter(FlagRaceRoomMember.room_id == room_id).all()
        for member in members:
            member.is_connected = True
            member.is_ready = True
        db.commit()
    active_user["id"] = 1
    response = client.post(f"/race-rooms/{room_id}/rounds")
    assert response.status_code == 201, response.text
    return response.json()


def test_create_join_reuse_and_room_capacity():
    room = create_and_join_two_players()
    assert room["code"] and len(room["code"]) == 6
    assert room["invite_url"].startswith("http")
    assert [member["user_id"] for member in room["members"]] == [1, 2]
    assert room["host_user_id"] == 1

    # Joining the same room is idempotent and does not duplicate a seat.
    repeated = client.post("/race-rooms/join", json={"code": room["code"]})
    assert repeated.status_code == 200
    assert len(repeated.json()["members"]) == 2

    for user_id in range(3, 9):
        active_user["id"] = user_id
        assert client.post("/race-rooms/join", json={"code": room["code"]}).status_code == 200
    active_user["id"] = 9
    full = client.post("/race-rooms/join", json={"code": room["code"]})
    assert full.status_code == 409
    assert full.json()["detail"] == "Race room is full"


def test_only_host_can_change_settings_and_change_clears_ready():
    room = create_and_join_two_players()
    with TestingSessionLocal() as db:
        for member in db.query(FlagRaceRoomMember).all():
            member.is_ready = True
        db.commit()

    active_user["id"] = 2
    assert client.patch(f"/race-rooms/{room['id']}", json={"difficulty": "hard"}).status_code == 403
    active_user["id"] = 1
    changed = client.patch(f"/race-rooms/{room['id']}", json={"difficulty": "hard"})
    assert changed.status_code == 200
    assert changed.json()["difficulty"] == "hard"
    assert all(not member["ready"] for member in changed.json()["members"])


def test_leaving_frees_the_seat_and_the_account_for_another_room():
    room = create_and_join_two_players()
    active_user["id"] = 2
    assert client.post(f"/race-rooms/{room['id']}/leave").status_code == 204
    active_user["id"] = 3
    joined = client.post("/race-rooms/join", json={"code": room["code"]})
    assert joined.status_code == 200
    assert next(member for member in joined.json()["members"] if member["user_id"] == 3)["seat"] == 2

    active_user["id"] = 2
    created = client.post("/race-rooms", json={"scope": "World", "difficulty": "easy"})
    assert created.status_code == 201


def test_invalid_expired_and_external_room_access_are_rejected():
    invalid = client.post("/race-rooms/join", json={"code": "ZZZZZZ"})
    assert invalid.status_code == 410

    room = create_and_join_two_players()
    with TestingSessionLocal() as db:
        stored = db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room["id"]).one()
        stored.expires_at = utc_now() - timedelta(seconds=1)
        for member in stored.members:
            member.is_connected = False
        db.commit()
    active_user["id"] = 3
    assert client.post("/race-rooms/join", json={"code": room["code"]}).status_code == 410
    assert client.get(f"/race-rooms/{room['id']}").status_code == 404


def test_assetlinks_uses_the_configured_staging_package(monkeypatch):
    fingerprint = ":".join([f"{value:02X}" for value in range(32)])
    monkeypatch.setattr(settings, "ANDROID_APP_LINK_PACKAGE_NAME", "com.enmanuelotero.atlasflags.staging")
    monkeypatch.setattr(settings, "ANDROID_APP_LINK_SHA256_CERT_FINGERPRINT", fingerprint)

    response = client.get("/.well-known/assetlinks.json")

    assert response.status_code == 200
    target = response.json()[0]["target"]
    assert target["package_name"] == "com.enmanuelotero.atlasflags.staging"
    assert target["sha256_cert_fingerprints"] == [fingerprint]


def test_round_has_shared_plan_and_personalized_option_order():
    room = create_and_join_two_players()
    race_round = prepare_round(room["id"])
    assert race_round["status"] == "countdown"
    assert len(race_round["plan"]) == RACE_FLAGS_TOTAL

    with TestingSessionLocal() as db:
        stored = db.query(FlagRaceRound).filter(FlagRaceRound.id == race_round["id"]).one()
        player_one = participant_plan(stored.plan, stored.id, 1, "secret")
        player_two = participant_plan(stored.plan, stored.id, 2, "secret")
        assert [item["country_code"] for item in player_one] == [item["country_code"] for item in player_two]
        assert all(set(one["option_codes"]) == set(two["option_codes"]) for one, two in zip(player_one, player_two))
        assert any(one["option_codes"] != two["option_codes"] for one, two in zip(player_one, player_two))


def test_wrong_answer_locks_player_and_final_answer_closes_once():
    room_payload = create_and_join_two_players()
    race_payload = prepare_round(room_payload["id"])
    with TestingSessionLocal() as db:
        room = db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room_payload["id"]).one()
        race_round = db.query(FlagRaceRound).filter(FlagRaceRound.id == race_payload["id"]).one()
        race_round.starts_at = utc_now() - timedelta(seconds=1)
        race_round.deadline_at = utc_now() + timedelta(seconds=90)
        participant = db.query(FlagRaceParticipant).filter(
            FlagRaceParticipant.round_id == race_round.id,
            FlagRaceParticipant.user_id == 1,
        ).one()
        question = race_round.plan[0]
        wrong_code = next(code for code in question["option_codes"] if code != question["country_code"])
        response_type, result = asyncio.run(process_answer(db, room, participant.user, RaceAnswerMessage(
            event_id="wrong-event-00000001",
            sequence=1,
            country_code=question["country_code"],
            selected_code=wrong_code,
        )))
        assert response_type == "answer_result"
        assert result["correct"] is False
        assert result["progress"] == 0
        assert result["mistakes"] == 1
        assert result["locked_until"] is not None

        participant.locked_until = utc_now() - timedelta(milliseconds=1)
        participant.progress = RACE_FLAGS_TOTAL - 1
        participant.discarded_codes = []
        participant.progress_reached_at = utc_now()
        final_question = race_round.plan[-1]
        response_type, result = asyncio.run(process_answer(db, room, participant.user, RaceAnswerMessage(
            event_id="final-event-00000001",
            sequence=2,
            country_code=final_question["country_code"],
            selected_code=final_question["country_code"],
        )))
        assert response_type == "race_finished"
        assert result["reason"] == "completed"
        assert result["winner_user_id"] == 1
        assert db.query(FlagRaceRound).filter(FlagRaceRound.id == race_round.id).one().status == "finished"
        assert db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room.id).one().status == "waiting"

        repeated_type, repeated = asyncio.run(process_answer(db, room, participant.user, RaceAnswerMessage(
            event_id="final-event-00000001",
            sequence=2,
            country_code=final_question["country_code"],
            selected_code=final_question["country_code"],
        )))
        assert repeated_type == "race_finished"
        assert repeated["winner_user_id"] == 1


def test_rematch_reuses_room_and_members_with_a_new_plan():
    room_payload = create_and_join_two_players()
    first_payload = prepare_round(room_payload["id"])
    with TestingSessionLocal() as db:
        room = db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room_payload["id"]).one()
        first_round = db.query(FlagRaceRound).filter(FlagRaceRound.id == first_payload["id"]).one()
        first_round.starts_at = utc_now() - timedelta(seconds=1)
        first_round.deadline_at = utc_now() + timedelta(seconds=90)
        participant = db.query(FlagRaceParticipant).filter(
            FlagRaceParticipant.round_id == first_round.id,
            FlagRaceParticipant.user_id == 1,
        ).one()
        participant.progress = RACE_FLAGS_TOTAL - 1
        final_question = first_round.plan[-1]
        response_type, _ = asyncio.run(process_answer(db, room, participant.user, RaceAnswerMessage(
            event_id="rematch-final-event-01",
            sequence=1,
            country_code=final_question["country_code"],
            selected_code=final_question["country_code"],
        )))
        assert response_type == "race_finished"
        first_plan = first_round.plan
        for member in room.members:
            member.is_connected = True
            member.is_ready = True
        db.commit()

    active_user["id"] = 1
    second = client.post(f"/race-rooms/{room_payload['id']}/rounds")
    assert second.status_code == 201, second.text
    assert second.json()["number"] == 2
    assert second.json()["id"] != first_payload["id"]
    with TestingSessionLocal() as db:
        stored_room = db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room_payload["id"]).one()
        assert len(stored_room.members) == 2
        assert len(stored_room.rounds) == 2
        assert stored_room.rounds[-1].plan != first_plan


def test_rule_generator_never_leaves_the_selected_scope():
    plan = build_race_plan("Oceania", "hard", "round-seed")
    assert len(plan) == 12
    assert len({question["country_code"] for question in plan}) == 12
    assert all(len(set(question["option_codes"])) == 4 for question in plan)


def test_rules_simulate_one_hundred_full_rooms_without_diverging():
    scopes = ["World", "Americas", "Europe", "Asia", "Africa", "Oceania"]
    difficulties = ["easy", "normal", "hard"]
    for room_index in range(100):
        round_id = f"load-round-{room_index}"
        plan = build_race_plan(
            scopes[room_index % len(scopes)],
            difficulties[room_index % len(difficulties)],
            round_id,
        )
        canonical = [question["country_code"] for question in plan]
        for user_id in range(1, 9):
            player_plan = participant_plan(plan, round_id, user_id, "load-test-secret")
            assert [question["country_code"] for question in player_plan] == canonical
            assert all(len(set(question["option_codes"])) == 4 for question in player_plan)


def test_restart_expires_overdue_round_and_clears_ephemeral_presence():
    room_payload = create_and_join_two_players()
    race_payload = prepare_round(room_payload["id"])
    with TestingSessionLocal() as db:
        race_round = db.query(FlagRaceRound).filter(FlagRaceRound.id == race_payload["id"]).one()
        race_round.starts_at = utc_now() - timedelta(seconds=91)
        race_round.deadline_at = utc_now() - timedelta(seconds=1)
        participant = db.query(FlagRaceParticipant).filter(
            FlagRaceParticipant.round_id == race_round.id,
            FlagRaceParticipant.user_id == 1,
        ).one()
        participant.progress = 4
        for member in db.query(FlagRaceRoomMember).filter(FlagRaceRoomMember.room_id == room_payload["id"]):
            member.is_connected = True
            member.is_ready = True
            member.intermission_state = "ad_break"
        db.commit()

    result = asyncio.run(recover_race_state(TestingSessionLocal))

    assert result == {"scheduled_rounds": 0, "expired_rounds": 1}
    with TestingSessionLocal() as db:
        race_round = db.query(FlagRaceRound).filter(FlagRaceRound.id == race_payload["id"]).one()
        assert race_round.status == "expired"
        assert race_round.finish_reason == "timeout"
        assert race_round.winner_user_id == 1
        assert race_round.room.status == "waiting"
        members = db.query(FlagRaceRoomMember).filter(FlagRaceRoomMember.room_id == room_payload["id"]).all()
        assert all(not member.is_connected and not member.is_ready for member in members)
        assert all(member.intermission_state == "reviewing_result" for member in members)


def test_maintenance_closes_idle_rooms_and_applies_retention():
    room_payload = create_and_join_two_players()
    race_payload = prepare_round(room_payload["id"])
    now = utc_now()
    with TestingSessionLocal() as db:
        room = db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room_payload["id"]).one()
        race_round = db.query(FlagRaceRound).filter(FlagRaceRound.id == race_payload["id"]).one()
        room.status = "waiting"
        room.expires_at = now - timedelta(minutes=1)
        room.last_activity_at = now - timedelta(days=8)
        for member in room.members:
            member.is_connected = False
        race_round.status = "finished"
        race_round.finished_at = now - timedelta(days=8)
        race_round.finish_reason = "completed"
        db.add(FlagRaceAnswerEvent(
            event_id="retention-event-0001",
            round_id=race_round.id,
            user_id=1,
            sequence=1,
            country_code="uy",
            selected_code="uy",
            is_correct=True,
            accepted_at=now - timedelta(hours=25),
        ))
        db.commit()

    result = run_race_maintenance_once(TestingSessionLocal, now)

    assert result == {
        "closed_rooms": 1,
        "deleted_events": 1,
        "deleted_rounds": 1,
        "deleted_rooms": 1,
    }
    with TestingSessionLocal() as db:
        assert db.query(FlagRaceRoom).filter(FlagRaceRoom.id == room_payload["id"]).first() is None


def socket_protocol(user_id: int) -> list[str]:
    token = jwt.encode(
        {"sub": str(user_id), "purpose": "access", "exp": utc_now() + timedelta(minutes=5)},
        os.environ["SECRET_KEY"],
        algorithm="HS256",
    )
    encoded = base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")
    return ["atlas-race-v1", f"auth.{encoded}"]


def test_websocket_authenticates_members_and_broadcasts_ready_state():
    room = create_and_join_two_players()
    with client.websocket_connect(f"/race-rooms/{room['id']}/socket", subprotocols=socket_protocol(1)) as first:
        assert first.receive_json()["type"] == "snapshot"
        assert first.receive_json()["type"] == "lobby_state"
        with client.websocket_connect(f"/race-rooms/{room['id']}/socket", subprotocols=socket_protocol(2)) as second:
            assert second.receive_json()["type"] == "snapshot"
            assert first.receive_json()["type"] == "lobby_state"
            assert second.receive_json()["type"] == "lobby_state"
            first.send_json({"type": "ready", "ready": True, "protocol_version": 1})
            first_state = first.receive_json()
            second_state = second.receive_json()
            assert first_state["type"] == second_state["type"] == "lobby_state"
            assert next(member for member in first_state["room"]["members"] if member["user_id"] == 1)["ready"] is True

@pytest.mark.parametrize("player_count", [2, 8])
def test_staggered_ready_common_start_and_idempotent_retry(player_count, monkeypatch):
    from routers import race_rooms
    from utils.race_rules import RACE_COUNTDOWN_SECONDS, RACE_DURATION_SECONDS
    scheduled = []
    monkeypatch.setattr(race_rooms, "schedule_round_deadline", lambda *args: scheduled.append(args))
    room = create_and_join_two_players()
    for user_id in range(3, player_count + 1):
        active_user["id"] = user_id
        assert client.post("/race-rooms/join", json={"code": room["code"]}).status_code == 200
    active_user["id"] = 1
    for user_id in range(1, player_count + 1):
        with TestingSessionLocal() as db:
            member = db.query(FlagRaceRoomMember).filter_by(room_id=room["id"], user_id=user_id).one()
            member.is_ready = member.is_connected = True
            db.commit()
        if user_id < player_count:
            assert client.post(f"/race-rooms/{room['id']}/rounds").status_code == 409
    before = utc_now()
    first = client.post(f"/race-rooms/{room['id']}/rounds").json()
    second = client.post(f"/race-rooms/{room['id']}/rounds").json()
    assert first["id"] == second["id"]
    assert first["starts_at"] == second["starts_at"]
    assert len(scheduled) == 1
    with TestingSessionLocal() as db:
        stored = db.query(FlagRaceRound).one()
        assert stored.starts_at >= before + timedelta(seconds=RACE_COUNTDOWN_SECONDS)
        assert stored.deadline_at - stored.starts_at == timedelta(seconds=RACE_DURATION_SECONDS)
        assert len(stored.participants) == player_count
    for user_id in range(1, player_count + 1):
        active_user["id"] = user_id
        snapshot = client.get(f"/race-rooms/{room['id']}").json()["current_round"]
        assert (snapshot["id"], snapshot["starts_at"], snapshot["deadline_at"]) == (first["id"], first["starts_at"], first["deadline_at"])


def test_server_rejects_early_old_round_out_of_order_and_late_answers(monkeypatch):
    from fastapi import HTTPException
    from routers import race_rooms
    room_payload = create_and_join_two_players()
    race_payload = prepare_round(room_payload["id"])
    with TestingSessionLocal() as db:
        room = db.query(FlagRaceRoom).one()
        race_round = db.query(FlagRaceRound).one()
        player = db.query(FlagRaceParticipant).filter_by(user_id=1).one()
        question = race_round.plan[0]
        message = RaceAnswerMessage(round_id=race_payload["id"], event_id="timing-event-0000001", sequence=1,
                                    country_code=question["country_code"], selected_code=question["country_code"])
        monkeypatch.setattr(race_rooms, "utc_now", lambda: race_round.starts_at - timedelta(milliseconds=1))
        with pytest.raises(HTTPException, match="countdown"):
            asyncio.run(process_answer(db, room, player.user, message))
        monkeypatch.setattr(race_rooms, "utc_now", lambda: race_round.starts_at)
        with pytest.raises(HTTPException, match="another round"):
            asyncio.run(process_answer(db, room, player.user, message.model_copy(update={"round_id": "old-round"})))
        with pytest.raises(HTTPException, match="out of order"):
            asyncio.run(process_answer(db, room, player.user, message.model_copy(update={"sequence": 2})))
        _, first = asyncio.run(process_answer(db, room, player.user, message))
        _, duplicate = asyncio.run(process_answer(db, room, player.user, message))
        assert first["progress"] == duplicate["progress"] == 1
        assert duplicate["expected_sequence"] == 2
        assert duplicate["mistakes"] == 0
        assert db.query(FlagRaceAnswerEvent).count() == 1
        monkeypatch.setattr(race_rooms, "utc_now", lambda: race_round.deadline_at)
        kind, final = asyncio.run(process_answer(db, room, player.user,
            message.model_copy(update={"event_id": "timing-event-0000002", "sequence": 2})))
        assert kind == "race_finished" and final["reason"] == "timeout"
        assert db.query(FlagRaceAnswerEvent).count() == 1


def test_clock_probe_echo_and_reconnection_preserve_official_time():
    room = create_and_join_two_players()
    started = prepare_round(room["id"])
    for _ in range(2):
        with client.websocket_connect(f"/race-rooms/{room['id']}/socket", subprotocols=socket_protocol(1)) as ws:
            snapshot = ws.receive_json()["room"]
            ws.receive_json()
            assert snapshot["current_round"]["starts_at"] == started["starts_at"]
            assert snapshot["current_round"]["deadline_at"] == started["deadline_at"]
            assert snapshot["current_round"]["id"] == started["id"]
            ws.send_json({"type": "heartbeat", "protocol_version": 1, "probe_id": "clock-probe-1"})
            reply = ws.receive_json()
            assert reply["probe_id"] == "clock-probe-1"
            assert reply["server_received_at"] <= reply["server_time"]
            ws.send_json({"type": "ready", "protocol_version": 1, "ready": True})
            assert ws.receive_json()["type"] == "error"
    active_user["id"] = 1
    retry = client.post(f"/race-rooms/{room['id']}/rounds").json()
    assert retry["id"] == started["id"]
    assert retry["starts_at"] == started["starts_at"]


def test_departure_before_start_and_stale_lobby_revision():
    room = create_and_join_two_players()
    active_user["id"] = 2
    assert client.post(f"/race-rooms/{room['id']}/leave").status_code == 204
    active_user["id"] = 1
    assert client.post(f"/race-rooms/{room['id']}/rounds").status_code == 409
    active_user["id"] = 3
    client.post("/race-rooms/join", json={"code": room["code"]})
    with TestingSessionLocal() as db:
        for member in db.query(FlagRaceRoomMember).all():
            member.is_connected = member.is_ready = True
        db.commit()
    active_user["id"] = 1
    response = client.post(f"/race-rooms/{room['id']}/rounds", json={"expected_revision": room["revision"]})
    assert response.status_code == 409
    assert "Lobby changed" in response.json()["detail"]


def test_slow_peer_does_not_delay_other_countdowns():
    from routers.race_rooms import RaceConnectionManager

    async def scenario():
        release = asyncio.Event()
        delivered = asyncio.Event()
        class Peer:
            def __init__(self, slow=False):
                self.slow = slow
            async def send_json(self, message):
                if self.slow:
                    await release.wait()
                else:
                    delivered.set()
            async def close(self, **kwargs):
                pass
        manager = RaceConnectionManager()
        manager.connections["room"] = {1: Peer(True), 2: Peer()}
        task = asyncio.create_task(manager.broadcast("room", "countdown", {"starts_at": "shared"}, 3))
        await asyncio.wait_for(delivered.wait(), timeout=0.5)
        assert not task.done()
        release.set()
        await task
    asyncio.run(scenario())


def test_replacement_socket_does_not_inherit_ready_or_get_marked_disconnected():
    room = create_and_join_two_players()
    path = f"/race-rooms/{room['id']}/socket"
    with client.websocket_connect(path, subprotocols=socket_protocol(1)) as first:
        first.receive_json()
        first.receive_json()
        first.send_json({"type": "ready", "protocol_version": 1, "ready": True})
        assert first.receive_json()["room"]["members"][0]["ready"] is True
        with client.websocket_connect(path, subprotocols=socket_protocol(1)) as replacement:
            snapshot = replacement.receive_json()["room"]
            replacement.receive_json()
            assert snapshot["members"][0]["connected"] is True
            assert snapshot["members"][0]["ready"] is False
            replacement.send_json({"type": "snapshot", "protocol_version": 1})
            assert replacement.receive_json()["room"]["members"][0]["connected"] is True


def test_setting_broadcast_keeps_each_recipient_identity():
    room = create_and_join_two_players()
    path = f"/race-rooms/{room['id']}/socket"
    with client.websocket_connect(path, subprotocols=socket_protocol(1)) as first:
        first.receive_json(); first.receive_json()
        with client.websocket_connect(path, subprotocols=socket_protocol(2)) as second:
            second.receive_json(); second.receive_json(); first.receive_json()
            active_user["id"] = 1
            response = client.patch(f"/race-rooms/{room['id']}", json={"difficulty": "hard"})
            assert response.status_code == 200
            assert first.receive_json()["room"]["current_user_id"] == 1
            assert second.receive_json()["room"]["current_user_id"] == 2
