"""Private, real-time Flag Race rooms.

PostgreSQL remains authoritative. ``RaceConnectionManager`` only owns the live
WebSocket fan-out, so this MVP must run as one ASGI process until a shared
pub/sub adapter is introduced.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import secrets
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import quote
from uuid import uuid4

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from jwt.exceptions import InvalidTokenError
from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings
from db import models
from db.database import SessionLocal
from dependencies import get_current_active_user, get_db
from schemas import user_schema
from schemas.race import (
    RaceAnswerMessage,
    RaceStartRequest,
    RaceIntermissionMessage,
    RaceRoomCreate,
    RaceRoomJoin,
    RaceRoomResponse,
    RaceRoomUpdate,
)
from utils.race_rules import (
    RACE_COUNTDOWN_SECONDS,
    RACE_DURATION_SECONDS,
    RACE_FLAGS_TOTAL,
    RACE_MAX_PLAYERS,
    RACE_MIN_PLAYERS,
    RACE_RULESET_VERSION,
    RACE_WRONG_LOCK_SECONDS,
    build_race_plan,
    participant_plan,
    race_versions,
)
from utils.limiter import limiter

router = APIRouter(tags=["flag-race"])
logger = logging.getLogger("atlas.race")
PROTOCOL_VERSION = 1
JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SECRET = settings.SECRET_KEY.get_secret_value()
FERNET = Fernet(base64.urlsafe_b64encode(hashlib.sha256(SECRET.encode("utf-8")).digest()))


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def invitation_hash(value: str) -> str:
    return hmac.new(SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def protect_invitation(value: str) -> str:
    return FERNET.encrypt(value.encode("utf-8")).decode("ascii")


def reveal_invitation(value: str) -> str:
    try:
        return FERNET.decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        raise HTTPException(status_code=500, detail="Room invitation cannot be recovered") from error


def room_invite_url(room: models.FlagRaceRoom) -> str:
    base = (settings.RACE_INVITE_BASE_URL or settings.BASE_URL).rstrip("/")
    return f"{base}/race/{reveal_invitation(room.join_token_encrypted)}"


def active_members(db: Session, room_id: str) -> list[models.FlagRaceRoomMember]:
    return db.query(models.FlagRaceRoomMember).filter(
        models.FlagRaceRoomMember.room_id == room_id,
        models.FlagRaceRoomMember.left_at.is_(None),
    ).order_by(models.FlagRaceRoomMember.seat).all()


def room_is_expired(db: Session, room: models.FlagRaceRoom, now: datetime | None = None) -> bool:
    if room.expires_at > (now or utc_now()):
        return False
    return not db.query(models.FlagRaceRoomMember.user_id).filter(
        models.FlagRaceRoomMember.room_id == room.id,
        models.FlagRaceRoomMember.left_at.is_(None),
        models.FlagRaceRoomMember.is_connected.is_(True),
    ).first()


def membership_or_404(db: Session, room_id: str, user_id: int) -> models.FlagRaceRoomMember:
    member = db.query(models.FlagRaceRoomMember).filter(
        models.FlagRaceRoomMember.room_id == room_id,
        models.FlagRaceRoomMember.user_id == user_id,
        models.FlagRaceRoomMember.left_at.is_(None),
    ).first()
    if not member:
        raise HTTPException(status_code=404, detail="Race room not found")
    return member


def current_round(db: Session, room_id: str) -> models.FlagRaceRound | None:
    return db.query(models.FlagRaceRound).filter(
        models.FlagRaceRound.room_id == room_id,
    ).order_by(models.FlagRaceRound.round_number.desc()).first()


def display_name(user: models.User) -> str:
    return user.ranking_alias or user.username


def standings_for(db: Session, race_round: models.FlagRaceRound) -> list[dict]:
    participants = db.query(models.FlagRaceParticipant).filter(
        models.FlagRaceParticipant.round_id == race_round.id,
    ).all()
    participants.sort(key=lambda item: (-item.progress, item.mistakes, item.progress_reached_at, item.seat))
    standings: list[dict] = []
    previous_key = None
    current_rank = 0
    for index, participant in enumerate(participants, start=1):
        key = (participant.progress, participant.mistakes, participant.progress_reached_at)
        if key != previous_key:
            current_rank = index
            previous_key = key
        standings.append({
            "rank": current_rank,
            "user_id": participant.user_id,
            "display_name": display_name(participant.user),
            "avatar_url": f"/user/{participant.user_id}/profile_image" if participant.user.profile_image else None,
            "progress": participant.progress,
            "mistakes": participant.mistakes,
            "finished_at": iso(participant.finished_at),
        })
    return standings


def finish_round(db: Session, race_round: models.FlagRaceRound, reason: str, winner_user_id: int | None = None) -> dict:
    """Close a locked round and return its authoritative result payload."""
    if race_round.status in ("finished", "expired", "cancelled"):
        return {
            "round_id": race_round.id,
            "reason": race_round.finish_reason,
            "winner_user_id": race_round.winner_user_id,
            "standings": standings_for(db, race_round),
        }
    now = utc_now()
    race_round.status = "finished" if reason == "completed" else "expired"
    race_round.finish_reason = reason
    race_round.finished_at = now
    race_round.winner_user_id = winner_user_id
    race_round.revision += 1
    for participant in race_round.participants:
        participant.game_state = "finished" if participant.is_connected else "forfeited"
        participant.finished_at = now
    room = race_round.room
    room.status = "waiting"
    room.revision += 1
    room.last_activity_at = now
    for member in active_members(db, room.id):
        member.is_ready = False
        member.intermission_state = "reviewing_result"
        member.last_activity_at = now
    db.flush()
    return {
        "round_id": race_round.id,
        "reason": reason,
        "winner_user_id": winner_user_id,
        "standings": standings_for(db, race_round),
    }


def round_snapshot(db: Session, race_round: models.FlagRaceRound, user_id: int) -> dict:
    participant = db.query(models.FlagRaceParticipant).filter(
        models.FlagRaceParticipant.round_id == race_round.id,
        models.FlagRaceParticipant.user_id == user_id,
    ).first()
    payload = {
        "id": race_round.id,
        "number": race_round.round_number,
        "revision": race_round.room.revision,
        "status": race_round.status,
        "starts_at": iso(race_round.starts_at),
        "deadline_at": iso(race_round.deadline_at),
        "finished_at": iso(race_round.finished_at),
        "finish_reason": race_round.finish_reason,
        "winner_user_id": race_round.winner_user_id,
        **race_versions(),
    }
    if participant:
        payload["participant"] = {
            "progress": participant.progress,
            "mistakes": participant.mistakes,
            "expected_sequence": participant.expected_sequence,
            "discarded_codes": participant.discarded_codes or [],
            "locked_until": iso(participant.locked_until),
            "session_eligible": participant.game_state == "finished",
        }
        if race_round.status in ("countdown", "running"):
            payload["plan"] = participant_plan(race_round.plan, race_round.id, user_id, SECRET)
    if race_round.status in ("finished", "expired", "cancelled"):
        payload["standings"] = standings_for(db, race_round)
    return payload


def room_snapshot(db: Session, room: models.FlagRaceRoom, user_id: int) -> dict:
    members = active_members(db, room.id)
    latest_round = current_round(db, room.id)
    return {
        "id": room.id,
        "code": reveal_invitation(room.join_code_encrypted),
        "invite_url": room_invite_url(room),
        "status": room.status,
        "scope": room.scope,
        "difficulty": room.difficulty,
        "host_user_id": room.current_host_user_id,
        "current_user_id": user_id,
        "revision": room.revision,
        "expires_at": iso(room.expires_at),
        "members": [{
            "user_id": member.user_id,
            "display_name": display_name(member.user),
            "avatar_url": f"/user/{member.user_id}/profile_image" if member.user.profile_image else None,
            "seat": member.seat,
            "role": "host" if member.user_id == room.current_host_user_id else "player",
            "ready": member.is_ready,
            "connected": member.is_connected,
            "intermission_state": member.intermission_state,
        } for member in members],
        "current_round": round_snapshot(db, latest_round, user_id) if latest_round else None,
    }


class RaceConnectionManager:
    def __init__(self) -> None:
        self.connections: dict[str, dict[int, WebSocket]] = defaultdict(dict)

    async def connect(self, room_id: str, user_id: int, websocket: WebSocket) -> None:
        previous = self.connections[room_id].get(user_id)
        self.connections[room_id][user_id] = websocket
        if previous and previous is not websocket:
            try:
                await asyncio.wait_for(previous.close(code=4001, reason="Reconnected elsewhere"), timeout=1)
            except Exception:
                pass

    def disconnect(self, room_id: str, user_id: int, websocket: WebSocket) -> None:
        if self.connections.get(room_id, {}).get(user_id) is websocket:
            self.connections[room_id].pop(user_id, None)
            if not self.connections[room_id]:
                self.connections.pop(room_id, None)

    async def send(self, websocket: WebSocket, message_type: str, payload: dict, revision: int) -> None:
        await websocket.send_json({
            "type": message_type,
            "protocol_version": PROTOCOL_VERSION,
            "revision": revision,
            **payload,
        })

    async def broadcast(self, room_id: str, message_type: str, payload: dict, revision: int) -> None:
        await self.send_many([
            (user_id, websocket, payload)
            for user_id, websocket in list(self.connections.get(room_id, {}).items())
        ], message_type, revision)

    async def send_many(self, deliveries: list[tuple[int, WebSocket, dict]], message_type: str, revision: int) -> None:
        async def deliver(websocket: WebSocket, payload: dict) -> None:
            try:
                await asyncio.wait_for(self.send(websocket, message_type, payload, revision), timeout=2)
            except Exception:
                # Let the socket's own cleanup update presence. One slow peer
                # must neither hold up the other peers nor cancel the deadline.
                try:
                    await asyncio.wait_for(websocket.close(code=4000, reason="Slow connection"), timeout=1)
                except Exception:
                    pass
        await asyncio.gather(*(deliver(websocket, payload) for _, websocket, payload in deliveries))


race_connections = RaceConnectionManager()
race_deadline_tasks: dict[str, asyncio.Task] = {}
race_maintenance_task: asyncio.Task | None = None
RACE_MAINTENANCE_INTERVAL_SECONDS = 300


def require_race_feature() -> None:
    if settings.ENV == "production" and not settings.RACE_MODE_ENABLED:
        raise HTTPException(status_code=404, detail="Flag Race is not available")


def unique_room_credentials(db: Session) -> tuple[str, str]:
    for _ in range(20):
        code = "".join(secrets.choice(JOIN_ALPHABET) for _ in range(6))
        token = secrets.token_urlsafe(32)
        exists = db.query(models.FlagRaceRoom.id).filter(
            (models.FlagRaceRoom.join_code_hash == invitation_hash(code))
            | (models.FlagRaceRoom.join_token_hash == invitation_hash(token))
        ).first()
        if not exists:
            return code, token
    raise HTTPException(status_code=503, detail="Could not allocate a race room")


def conflicting_membership(db: Session, user_id: int, except_room_id: str | None = None) -> bool:
    query = db.query(models.FlagRaceRoomMember).join(models.FlagRaceRoom).filter(
        models.FlagRaceRoomMember.user_id == user_id,
        models.FlagRaceRoomMember.left_at.is_(None),
        models.FlagRaceRoom.status.in_(("waiting", "round_active")),
    )
    if except_room_id:
        query = query.filter(models.FlagRaceRoom.id != except_room_id)
    return any(not room_is_expired(db, member.room) for member in query.all())


@router.post("/race-rooms", response_model=RaceRoomResponse, status_code=201)
@limiter.limit("10/minute")
async def create_race_room(
    request: Request,
    payload: RaceRoomCreate,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    if conflicting_membership(db, current_user.id):
        raise HTTPException(status_code=409, detail="Leave the current race room first")
    code, token = unique_room_credentials(db)
    now = utc_now()
    room = models.FlagRaceRoom(
        id=str(uuid4()),
        join_code_hash=invitation_hash(code),
        join_code_encrypted=protect_invitation(code),
        join_token_hash=invitation_hash(token),
        join_token_encrypted=protect_invitation(token),
        owner_user_id=current_user.id,
        current_host_user_id=current_user.id,
        scope=payload.scope,
        difficulty=payload.difficulty,
        created_at=now,
        last_activity_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(room)
    db.add(models.FlagRaceRoomMember(
        room_id=room.id,
        user_id=current_user.id,
        seat=1,
        role="host",
        joined_at=now,
        last_activity_at=now,
    ))
    db.commit()
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room.id).first()
    return room_snapshot(db, room, current_user.id)


@router.post("/race-rooms/join", response_model=RaceRoomResponse)
@limiter.limit("30/minute")
async def join_race_room(
    request: Request,
    payload: RaceRoomJoin,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    lookup = invitation_hash(payload.code or payload.token or "")
    column = models.FlagRaceRoom.join_code_hash if payload.code else models.FlagRaceRoom.join_token_hash
    room = db.query(models.FlagRaceRoom).filter(column == lookup).with_for_update().first()
    if not room or room_is_expired(db, room) or room.status == "closed":
        raise HTTPException(status_code=410, detail="Race room expired or does not exist")
    existing = db.query(models.FlagRaceRoomMember).filter(
        models.FlagRaceRoomMember.room_id == room.id,
        models.FlagRaceRoomMember.user_id == current_user.id,
    ).first()
    if existing and existing.left_at is None:
        return room_snapshot(db, room, current_user.id)
    if room.status != "waiting":
        raise HTTPException(status_code=409, detail="Race already started")
    if conflicting_membership(db, current_user.id, room.id):
        raise HTTPException(status_code=409, detail="Leave the current race room first")
    members = active_members(db, room.id)
    if len(members) >= RACE_MAX_PLAYERS:
        raise HTTPException(status_code=409, detail="Race room is full")
    occupied = {member.seat for member in members}
    seat = next(value for value in range(1, RACE_MAX_PLAYERS + 1) if value not in occupied)
    now = utc_now()
    if existing:
        existing.seat = seat
        existing.left_at = None
        existing.is_ready = False
        existing.is_connected = False
        existing.intermission_state = "in_lobby"
        existing.joined_at = now
        existing.last_activity_at = now
    else:
        db.add(models.FlagRaceRoomMember(
            room_id=room.id,
            user_id=current_user.id,
            seat=seat,
            joined_at=now,
            last_activity_at=now,
        ))
    room.revision += 1
    room.last_activity_at = now
    room.expires_at = now + timedelta(minutes=30)
    db.commit()
    return room_snapshot(db, room, current_user.id)


@router.get("/race-rooms/{room_id}", response_model=RaceRoomResponse)
async def get_race_room(
    room_id: str,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    membership_or_404(db, room_id, current_user.id)
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).first()
    if room_is_expired(db, room):
        raise HTTPException(status_code=410, detail="Race room expired")
    return room_snapshot(db, room, current_user.id)


@router.patch("/race-rooms/{room_id}", response_model=RaceRoomResponse)
async def update_race_room(
    room_id: str,
    payload: RaceRoomUpdate,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
    membership_or_404(db, room_id, current_user.id)
    if room.current_host_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the host can change race settings")
    if room.status != "waiting":
        raise HTTPException(status_code=409, detail="Race settings are locked")
    changed = (payload.scope is not None and payload.scope != room.scope) or (
        payload.difficulty is not None and payload.difficulty != room.difficulty
    )
    if payload.scope is not None:
        room.scope = payload.scope
    if payload.difficulty is not None:
        room.difficulty = payload.difficulty
    if changed:
        for member in active_members(db, room.id):
            member.is_ready = False
        room.revision += 1
    room.last_activity_at = utc_now()
    db.commit()
    snapshot = room_snapshot(db, room, current_user.id)
    await broadcast_lobby(db, room)
    return snapshot


@router.post("/race-rooms/{room_id}/leave", status_code=204)
async def leave_race_room(
    room_id: str,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
    member = membership_or_404(db, room_id, current_user.id)
    if room.status != "waiting":
        raise HTTPException(status_code=409, detail="A running race cannot be left through the lobby")
    now = utc_now()
    db.delete(member)
    db.flush()
    remaining = active_members(db, room.id)
    if remaining and room.current_host_user_id == current_user.id:
        room.current_host_user_id = remaining[0].user_id
        remaining[0].role = "host"
    if not remaining:
        room.expires_at = now + timedelta(days=7 if room.is_persistent else 0, minutes=30 if not room.is_persistent else 0)
    room.revision += 1
    room.last_activity_at = now
    db.commit()
    await broadcast_lobby(db, room)


@router.post("/race-rooms/{room_id}/rounds", status_code=201)
async def start_race_round(
    room_id: str,
    payload: RaceStartRequest | None = None,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
    membership_or_404(db, room_id, current_user.id)
    if room.current_host_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the host can start the race")
    if room.status == "round_active":
        # Retrying a start cannot allocate another round or move its timestamps.
        return round_snapshot(db, current_round(db, room.id), current_user.id)
    if room.status != "waiting":
        raise HTTPException(status_code=409, detail="Room is not waiting")
    if payload and payload.expected_revision is not None and payload.expected_revision != room.revision:
        raise HTTPException(status_code=409, detail="Lobby changed; refresh before starting")
    members = active_members(db, room.id)
    if not RACE_MIN_PLAYERS <= len(members) <= RACE_MAX_PLAYERS:
        raise HTTPException(status_code=409, detail="A race needs between 2 and 8 players")
    if any(not member.is_ready or not member.is_connected for member in members):
        raise HTTPException(status_code=409, detail="Every player must be connected and ready")
    round_number = (db.query(func.max(models.FlagRaceRound.round_number)).filter(
        models.FlagRaceRound.room_id == room.id,
    ).scalar() or 0) + 1
    now = utc_now()
    starts_at = now + timedelta(seconds=RACE_COUNTDOWN_SECONDS)
    race_round = models.FlagRaceRound(
        id=str(uuid4()),
        room_id=room.id,
        round_number=round_number,
        status="countdown",
        ruleset_version=RACE_RULESET_VERSION,
        content_version=race_versions()["content_version"],
        plan=[],
        created_at=now,
        starts_at=starts_at,
        deadline_at=starts_at + timedelta(seconds=RACE_DURATION_SECONDS),
    )
    race_round.plan = build_race_plan(room.scope, room.difficulty, race_round.id)
    db.add(race_round)
    for member in members:
        db.add(models.FlagRaceParticipant(
            round_id=race_round.id,
            user_id=member.user_id,
            seat=member.seat,
            progress_reached_at=starts_at,
            is_connected=member.is_connected,
            joined_at=now,
            last_activity_at=now,
        ))
        member.is_ready = False
    room.status = "round_active"
    room.revision += 1
    room.last_activity_at = now
    db.commit()
    # Schedule before any network I/O. Delivery failure never removes expiry.
    schedule_round_deadline(room.id, race_round.id, race_round.deadline_at)
    response = round_snapshot(db, race_round, current_user.id)
    revision = room.revision
    deliveries = [
        (member.user_id, websocket, {"room_id": room.id, "round": round_snapshot(db, race_round, member.user_id)})
        for member in members
        if (websocket := race_connections.connections.get(room.id, {}).get(member.user_id))
    ]
    db.rollback()  # End snapshot reads before awaiting network sends.
    await race_connections.send_many(deliveries, "countdown", revision)
    return response


@router.get("/race-rooms/{room_id}/rounds/{round_id}/results")
async def get_race_results(
    room_id: str,
    round_id: str,
    current_user: user_schema.User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    _: None = Depends(require_race_feature),
):
    membership_or_404(db, room_id, current_user.id)
    race_round = db.query(models.FlagRaceRound).filter(
        models.FlagRaceRound.id == round_id,
        models.FlagRaceRound.room_id == room_id,
    ).first()
    if not race_round or race_round.status not in ("finished", "expired", "cancelled"):
        raise HTTPException(status_code=404, detail="Race result not found")
    return round_snapshot(db, race_round, current_user.id)


def timeout_winner(db: Session, round_id: str) -> int | None:
    participants = db.query(models.FlagRaceParticipant).filter(
        models.FlagRaceParticipant.round_id == round_id,
    ).all()
    ranked = sorted(
        participants,
        key=lambda item: (-item.progress, item.mistakes, item.progress_reached_at, item.seat),
    )
    return ranked[0].user_id if ranked and ranked[0].progress > 0 else None


async def finish_after_deadline(
    room_id: str,
    round_id: str,
    deadline_at: datetime,
    session_factory: Callable[[], Session] = SessionLocal,
) -> None:
    while (delay := (deadline_at - utc_now()).total_seconds()) > 0:
        await asyncio.sleep(min(delay, 1.0))
    db = session_factory()
    try:
        db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
        race_round = db.query(models.FlagRaceRound).filter(models.FlagRaceRound.id == round_id).with_for_update().first()
        if not race_round or race_round.status in ("finished", "expired", "cancelled"):
            return
        winner_user_id = timeout_winner(db, round_id)
        result = finish_round(db, race_round, "timeout", winner_user_id)
        db.commit()
        await race_connections.broadcast(room_id, "race_finished", result, race_round.room.revision)
    finally:
        db.close()


def schedule_round_deadline(
    room_id: str,
    round_id: str,
    deadline_at: datetime,
    session_factory: Callable[[], Session] = SessionLocal,
) -> asyncio.Task:
    existing = race_deadline_tasks.get(round_id)
    if existing and not existing.done():
        return existing
    task = asyncio.create_task(finish_after_deadline(room_id, round_id, deadline_at, session_factory))
    race_deadline_tasks[round_id] = task

    def forget(completed: asyncio.Task) -> None:
        if race_deadline_tasks.get(round_id) is completed:
            race_deadline_tasks.pop(round_id, None)

    task.add_done_callback(forget)
    return task


async def recover_race_state(session_factory: Callable[[], Session] = SessionLocal) -> dict[str, int]:
    """Reconcile durable race state after a process restart."""
    now = utc_now()
    pending: list[tuple[str, str, datetime]] = []
    recovered = 0
    expired = 0
    db = session_factory()
    try:
        db.query(models.FlagRaceRoomMember).update({
            models.FlagRaceRoomMember.is_connected: False,
            models.FlagRaceRoomMember.is_ready: False,
            models.FlagRaceRoomMember.intermission_state: "in_lobby",
        }, synchronize_session=False)
        active_rounds = db.query(models.FlagRaceRound).filter(
            models.FlagRaceRound.status.in_(("countdown", "running")),
        ).all()
        active_ids = [race_round.id for race_round in active_rounds]
        if active_ids:
            db.query(models.FlagRaceParticipant).filter(
                models.FlagRaceParticipant.round_id.in_(active_ids),
            ).update({models.FlagRaceParticipant.is_connected: False}, synchronize_session=False)
        for race_round in active_rounds:
            if race_round.deadline_at <= now:
                finish_round(db, race_round, "timeout", timeout_winner(db, race_round.id))
                expired += 1
            else:
                race_round.room.status = "round_active"
                pending.append((race_round.room_id, race_round.id, race_round.deadline_at))
                recovered += 1
        db.commit()
    finally:
        db.close()
    for room_id, round_id, deadline_at in pending:
        schedule_round_deadline(room_id, round_id, deadline_at, session_factory)
    return {"scheduled_rounds": recovered, "expired_rounds": expired}


def run_race_maintenance_once(
    session_factory: Callable[[], Session] = SessionLocal,
    now: datetime | None = None,
) -> dict[str, int]:
    """Apply bounded retention and close abandoned, expired lobbies."""
    current_time = now or utc_now()
    db = session_factory()
    try:
        closed_rooms = db.query(models.FlagRaceRoom).filter(
            models.FlagRaceRoom.status == "waiting",
            models.FlagRaceRoom.expires_at <= current_time,
            ~models.FlagRaceRoom.members.any(
                models.FlagRaceRoomMember.is_connected.is_(True),
            ),
        ).update({models.FlagRaceRoom.status: "closed"}, synchronize_session=False)
        deleted_events = db.query(models.FlagRaceAnswerEvent).filter(
            models.FlagRaceAnswerEvent.accepted_at < current_time - timedelta(hours=24),
        ).delete(synchronize_session=False)
        old_rounds = db.query(models.FlagRaceRound).filter(
            models.FlagRaceRound.finished_at.is_not(None),
            models.FlagRaceRound.finished_at < current_time - timedelta(days=7),
        ).all()
        for race_round in old_rounds:
            db.delete(race_round)
        db.flush()
        removable_rooms = db.query(models.FlagRaceRoom).filter(
            models.FlagRaceRoom.status == "closed",
            models.FlagRaceRoom.last_activity_at < current_time - timedelta(days=7),
            ~models.FlagRaceRoom.rounds.any(),
        ).all()
        for room in removable_rooms:
            db.delete(room)
        db.commit()
        return {
            "closed_rooms": closed_rooms,
            "deleted_events": deleted_events,
            "deleted_rounds": len(old_rounds),
            "deleted_rooms": len(removable_rooms),
        }
    finally:
        db.close()


async def race_maintenance_loop(session_factory: Callable[[], Session] = SessionLocal) -> None:
    while True:
        await asyncio.sleep(RACE_MAINTENANCE_INTERVAL_SECONDS)
        try:
            run_race_maintenance_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Flag Race maintenance failed")


async def start_race_background_tasks(session_factory: Callable[[], Session] = SessionLocal) -> None:
    global race_maintenance_task
    await recover_race_state(session_factory)
    run_race_maintenance_once(session_factory)
    if not race_maintenance_task or race_maintenance_task.done():
        race_maintenance_task = asyncio.create_task(race_maintenance_loop(session_factory))


async def stop_race_background_tasks() -> None:
    global race_maintenance_task
    tasks = [*race_deadline_tasks.values()]
    race_deadline_tasks.clear()
    if race_maintenance_task:
        tasks.append(race_maintenance_task)
        race_maintenance_task = None
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def clear_intermission_after(room_id: str, user_id: int, expected_state: str) -> None:
    await asyncio.sleep(90)
    db = SessionLocal()
    try:
        member = db.query(models.FlagRaceRoomMember).filter(
            models.FlagRaceRoomMember.room_id == room_id,
            models.FlagRaceRoomMember.user_id == user_id,
            models.FlagRaceRoomMember.left_at.is_(None),
        ).first()
        room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).first()
        if not member or not room or member.intermission_state != expected_state:
            return
        member.intermission_state = "in_lobby"
        room.revision += 1
        db.commit()
        await race_connections.broadcast(room_id, "player_intermission_changed", {
            "user_id": user_id,
            "state": "in_lobby",
        }, room.revision)
    finally:
        db.close()


def websocket_token(websocket: WebSocket) -> str | None:
    protocols = [value.strip() for value in websocket.headers.get("sec-websocket-protocol", "").split(",")]
    encoded = next((value[5:] for value in protocols if value.startswith("auth.")), None)
    if not encoded:
        return None
    try:
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def websocket_user(db: Session, websocket: WebSocket) -> models.User | None:
    token = websocket_token(websocket)
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") not in (None, "access"):
            return None
        user_id = int(payload["sub"])
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        return None
    return db.query(models.User).filter(models.User.id == user_id, models.User.is_active.is_(True)).first()


async def broadcast_lobby(db: Session, room: models.FlagRaceRoom) -> None:
    revision = room.revision
    deliveries = [
        (user_id, websocket, {"room": room_snapshot(db, room, user_id)})
        for user_id, websocket in list(race_connections.connections.get(room.id, {}).items())
    ]
    db.rollback()
    await race_connections.send_many(deliveries, "lobby_state", revision)


async def process_answer(db: Session, room: models.FlagRaceRoom, user: models.User, message: RaceAnswerMessage) -> tuple[str, dict]:
    race_round = db.query(models.FlagRaceRound).filter(
        models.FlagRaceRound.room_id == room.id,
    ).with_for_update().order_by(models.FlagRaceRound.round_number.desc()).first()
    if not race_round:
        raise HTTPException(status_code=409, detail="No active race")
    if message.round_id is not None and message.round_id != race_round.id:
        raise HTTPException(status_code=409, detail="Answer belongs to another round")
    participant = db.query(models.FlagRaceParticipant).filter(
        models.FlagRaceParticipant.round_id == race_round.id,
        models.FlagRaceParticipant.user_id == user.id,
    ).with_for_update().first()
    if not participant:
        raise HTTPException(status_code=403, detail="Player is not in this race")
    duplicate = db.query(models.FlagRaceAnswerEvent).filter(
        models.FlagRaceAnswerEvent.event_id == message.event_id,
    ).first()
    if duplicate:
        if duplicate.round_id != race_round.id or duplicate.user_id != user.id or duplicate.sequence != message.sequence:
            raise HTTPException(status_code=409, detail="event_id was already used")
        if race_round.status in ("finished", "expired", "cancelled"):
            return "race_finished", {
                "round_id": race_round.id,
                "reason": race_round.finish_reason,
                "winner_user_id": race_round.winner_user_id,
                "standings": standings_for(db, race_round),
            }
        return "answer_result", {
            "round_id": race_round.id,
            "event_id": duplicate.event_id,
            "correct": duplicate.is_correct,
            "locked_until": iso(participant.locked_until),
            "progress": participant.progress,
            "mistakes": participant.mistakes,
            "discarded_codes": participant.discarded_codes or [],
            "expected_sequence": participant.expected_sequence,
        }
    if race_round.status not in ("countdown", "running"):
        raise HTTPException(status_code=409, detail="No active race")
    now = utc_now()
    if now < race_round.starts_at:
        raise HTTPException(status_code=409, detail="Race countdown is still active")
    if now >= race_round.deadline_at:
        ranked = sorted(race_round.participants, key=lambda item: (-item.progress, item.mistakes, item.progress_reached_at, item.seat))
        winner = ranked[0].user_id if ranked and ranked[0].progress > 0 else None
        result = finish_round(db, race_round, "timeout", winner)
        db.commit()
        return "race_finished", result
    if participant.locked_until and participant.locked_until > now:
        raise HTTPException(status_code=409, detail="Answer penalty is still active")
    if message.sequence != participant.expected_sequence:
        raise HTTPException(status_code=409, detail="Answer sequence is out of order")
    if participant.progress >= RACE_FLAGS_TOTAL:
        raise HTTPException(status_code=409, detail="Player already finished")
    question = race_round.plan[participant.progress]
    country_code = message.country_code.lower()
    selected_code = message.selected_code.lower()
    if country_code != question["country_code"] or selected_code not in question["option_codes"]:
        raise HTTPException(status_code=422, detail="Answer does not match the current server question")
    if selected_code in (participant.discarded_codes or []):
        raise HTTPException(status_code=409, detail="Answer option was already discarded")

    correct = selected_code == country_code
    locked_until = None
    if correct:
        participant.locked_until = None
        participant.progress += 1
        participant.discarded_codes = []
        participant.progress_reached_at = now
    else:
        participant.mistakes += 1
        participant.discarded_codes = [*(participant.discarded_codes or []), selected_code]
        locked_until = now + timedelta(seconds=RACE_WRONG_LOCK_SECONDS)
        participant.locked_until = locked_until
    participant.expected_sequence += 1
    participant.last_activity_at = now
    db.add(models.FlagRaceAnswerEvent(
        event_id=message.event_id,
        round_id=race_round.id,
        user_id=user.id,
        sequence=message.sequence,
        country_code=country_code,
        selected_code=selected_code,
        is_correct=correct,
        accepted_at=now,
        locked_until=locked_until,
    ))
    if race_round.status == "countdown":
        race_round.status = "running"
    race_round.revision += 1
    room.revision += 1
    if participant.progress == RACE_FLAGS_TOTAL:
        participant.finished_at = now
        result = finish_round(db, race_round, "completed", user.id)
        db.commit()
        return "race_finished", result
    db.commit()
    next_question = participant_plan(race_round.plan, race_round.id, user.id, SECRET)[participant.progress] if correct else None
    return "answer_result", {
        "round_id": race_round.id,
        "event_id": message.event_id,
        "correct": correct,
        "locked_until": iso(locked_until),
        "progress": participant.progress,
        "mistakes": participant.mistakes,
        "expected_sequence": participant.expected_sequence,
        "discarded_codes": participant.discarded_codes or [],
        "next_question": next_question,
    }


@router.websocket("/race-rooms/{room_id}/socket")
async def race_socket(websocket: WebSocket, room_id: str):
    if settings.ENV == "production" and not settings.RACE_MODE_ENABLED:
        await websocket.close(code=4404, reason="Flag Race is not available")
        return
    session_factory = getattr(websocket.app.state, "race_session_factory", SessionLocal)
    db = session_factory()
    user = websocket_user(db, websocket)
    if not user:
        await websocket.close(code=4401, reason="Authentication required")
        db.close()
        return
    try:
        member = membership_or_404(db, room_id, user.id)
        room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).first()
        if room_is_expired(db, room):
            raise HTTPException(status_code=410, detail="Race room expired")
    except HTTPException:
        await websocket.close(code=4404, reason="Race room not found")
        db.close()
        return
    await websocket.accept(subprotocol="atlas-race-v1")
    user_id = user.id
    db.rollback()
    await race_connections.connect(room_id, user_id, websocket)
    room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
    member = membership_or_404(db, room_id, user_id)
    member.is_ready = False
    member.is_connected = True
    member.intermission_state = "in_lobby"
    member.last_activity_at = utc_now()
    active_participant = db.query(models.FlagRaceParticipant).join(models.FlagRaceRound).filter(
        models.FlagRaceParticipant.user_id == user.id,
        models.FlagRaceRound.room_id == room_id,
        models.FlagRaceRound.status.in_(("countdown", "running")),
    ).first()
    if active_participant:
        active_participant.is_connected = True
    room.revision += 1
    db.commit()
    await race_connections.send(websocket, "snapshot", {"room": room_snapshot(db, room, user.id)}, room.revision)
    await broadcast_lobby(db, room)
    answer_times: list[float] = []
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                await websocket.close(code=4000, reason="Heartbeat timeout")
                break
            try:
                received_at = utc_now()
                message = json.loads(raw)
                if not isinstance(message, dict):
                    raise ValueError("Expected a JSON object")
                if race_connections.connections.get(room_id, {}).get(user_id) is not websocket:
                    break
                # The session lives as long as the socket, but its transaction
                # and cached identities must not live across incoming frames.
                db.rollback()
                room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
                member = membership_or_404(db, room_id, user_id)
                message_type = message.get("type")
                if message.get("protocol_version") != PROTOCOL_VERSION:
                    raise HTTPException(status_code=409, detail="Unsupported race protocol")
                if message_type == "heartbeat":
                    member.last_activity_at = utc_now()
                    db.commit()
                    revision = room.revision
                    db.rollback()
                    await race_connections.send(websocket, "heartbeat", {
                        "probe_id": str(message.get("probe_id", ""))[:64],
                        "server_received_at": iso(received_at),
                        "server_time": iso(utc_now()),
                    }, revision)
                elif message_type == "ready":
                    if room.status != "waiting":
                        raise HTTPException(status_code=409, detail="Race is not in the lobby")
                    if not isinstance(message.get("ready"), bool):
                        raise ValueError("ready must be a boolean")
                    member.is_ready = message["ready"]
                    member.intermission_state = "in_lobby"
                    room.revision += 1
                    db.commit()
                    await broadcast_lobby(db, room)
                elif message_type == "intermission_status":
                    status_message = RaceIntermissionMessage.model_validate({"state": message.get("state")})
                    member.intermission_state = status_message.state
                    member.last_activity_at = utc_now()
                    room.revision += 1
                    db.commit()
                    await race_connections.broadcast(room.id, "player_intermission_changed", {
                        "user_id": user.id,
                        "state": status_message.state,
                    }, room.revision)
                    if status_message.state == "ad_break":
                        asyncio.create_task(clear_intermission_after(room.id, user.id, status_message.state))
                elif message_type == "answer":
                    monotonic_now = asyncio.get_running_loop().time()
                    answer_times = [value for value in answer_times if monotonic_now - value < 5]
                    if len(answer_times) >= 30:
                        raise HTTPException(status_code=429, detail="Too many race answers")
                    answer_times.append(monotonic_now)
                    answer = RaceAnswerMessage.model_validate({
                        key: message.get(key)
                        for key in ("round_id", "event_id", "sequence", "country_code", "selected_code")
                    })
                    response_type, payload = await process_answer(db, room, user, answer)
                    if response_type == "race_finished":
                        await race_connections.broadcast(room.id, response_type, payload, room.revision)
                    else:
                        await race_connections.send(websocket, response_type, payload, room.revision)
                        progress = [{
                            "user_id": item.user_id,
                            "progress": item.progress,
                            "mistakes": item.mistakes,
                        } for item in db.query(models.FlagRaceParticipant).filter(
                            models.FlagRaceParticipant.round_id == current_round(db, room.id).id,
                        ).all()]
                        await race_connections.broadcast(room.id, "progress", {"round_id": current_round(db, room.id).id, "participants": progress}, room.revision)
                elif message_type == "snapshot":
                    snapshot = room_snapshot(db, room, user_id)
                    revision = room.revision
                    db.rollback()
                    await race_connections.send(websocket, "snapshot", {"room": snapshot}, revision)
                else:
                    raise HTTPException(status_code=422, detail="Unknown race message")
            except (ValueError, TypeError) as error:
                db.rollback()
                await race_connections.send(websocket, "error", {"code": "invalid_message", "message": str(error)}, room.revision)
            except HTTPException as error:
                db.rollback()
                await race_connections.send(websocket, "error", {
                    "code": f"http_{error.status_code}",
                    "message": str(error.detail),
                }, room.revision)
            finally:
                db.rollback()
    except WebSocketDisconnect:
        pass
    finally:
        # An old socket closing after replacement cannot disconnect the new one.
        if race_connections.connections.get(room_id, {}).get(user_id) is not websocket:
            db.close()
            return
        race_connections.disconnect(room_id, user_id, websocket)
        try:
            db.rollback()
            room = db.query(models.FlagRaceRoom).filter(models.FlagRaceRoom.id == room_id).with_for_update().first()
            member = membership_or_404(db, room_id, user_id)
            member.is_connected = False
            member.is_ready = False
            member.intermission_state = "in_lobby"
            active_participant = db.query(models.FlagRaceParticipant).join(models.FlagRaceRound).filter(
                models.FlagRaceParticipant.user_id == user.id,
                models.FlagRaceRound.room_id == room_id,
                models.FlagRaceRound.status.in_(("countdown", "running")),
            ).first()
            if active_participant:
                active_participant.is_connected = False
            room.revision += 1
            db.flush()
            if not db.query(models.FlagRaceRoomMember.user_id).filter(
                models.FlagRaceRoomMember.room_id == room_id,
                models.FlagRaceRoomMember.left_at.is_(None),
                models.FlagRaceRoomMember.is_connected.is_(True),
            ).first():
                room.expires_at = utc_now() + timedelta(days=7 if room.is_persistent else 0, minutes=30 if not room.is_persistent else 0)
            db.commit()
            await broadcast_lobby(db, room)
        except Exception:
            db.rollback()
        db.close()


@router.get("/race/{token}", response_class=HTMLResponse, include_in_schema=False)
async def race_invitation_page(token: str, request: Request, db: Session = Depends(get_db)):
    require_race_feature()
    room = db.query(models.FlagRaceRoom).filter(
        models.FlagRaceRoom.join_token_hash == invitation_hash(token),
    ).first()
    if not room or room_is_expired(db, room) or room.status == "closed":
        return HTMLResponse("<!doctype html><html lang='es'><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Invitación vencida</title><main><h1>Esta carrera ya no está disponible</h1><p>Pide a quien te invitó que comparta una sala nueva.</p></main></html>", status_code=410)
    members = active_members(db, room.id)
    inviter = db.query(models.User).filter(models.User.id == room.owner_user_id).first()
    code = reveal_invitation(room.join_code_encrypted)
    referrer = quote(f"race_invite={token}", safe="")
    play_url = f"https://play.google.com/store/apps/details?id=com.enmanuelotero.atlasflags&referrer={referrer}"
    title = f"{display_name(inviter)} te invita" if inviter else "Te invitaron a una carrera"
    return HTMLResponse(f"""<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Carrera de Banderas</title><style>body{{font-family:system-ui;background:#f5f7f2;color:#172033;margin:0}}main{{max-width:32rem;margin:8vh auto;padding:2rem}}.card{{background:white;border-radius:1.5rem;padding:2rem;box-shadow:0 16px 50px #17203318}}.code{{font:700 2rem monospace;letter-spacing:.2em}}a{{display:block;background:#166534;color:white;text-align:center;text-decoration:none;padding:1rem;border-radius:1rem;font-weight:700}}</style><main><section class="card"><p>CARRERA PRIVADA</p><h1>{html.escape(title)}</h1><p>{html.escape(room.scope)} · {html.escape(room.difficulty.title())} · {len(members)}/8 personas</p><p>Código</p><p class="code">{html.escape(code)}</p><a href="{html.escape(play_url)}">Instalar desde Google Play</a><p>Si ya tienes la app, vuelve a abrir este enlace o introduce el código.</p></section></main></html>""")


@router.get("/.well-known/assetlinks.json", include_in_schema=False)
async def android_asset_links():
    fingerprint = settings.ANDROID_APP_LINK_SHA256_CERT_FINGERPRINT
    if not fingerprint:
        return JSONResponse({"detail": "App Links are not configured"}, status_code=503)
    return [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": settings.ANDROID_APP_LINK_PACKAGE_NAME,
            "sha256_cert_fingerprints": [fingerprint],
        },
    }]
