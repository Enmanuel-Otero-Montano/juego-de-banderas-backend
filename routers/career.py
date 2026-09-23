# -*- coding: utf-8 -*-
"""
Career Mode Router

Endpoints exclusivos para modo carrera.
Requieren game_mode == "career" explícito, NO aceptan inferencia.

El frontend detecta el modo por URL (career-mode), pero el backend NO confía
y valida game_mode explícitamente en cada request.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import Annotated, Literal, Optional
from datetime import datetime, timezone

from schemas import user_schema
from schemas.score import (
    CareerAttemptCreate,
    CareerAttemptResponse,
    StageCompleteRequest,
    CareerStatsResponse,
    CareerLeaderboardResponse,
    CareerMeStatsResponse,
    CareerRunHistoryResponse,
    RankingProfileUpdate,
    RankingProfileResponse,
)
from db.models import CareerAttempt, CareerSeasonProfile, StageBest, StageRun
from dependencies import get_current_active_user, get_db
from utils.limiter import limiter
from utils.career_rules import (
    ATTEMPT_TTL_SECONDS,
    CURRENT_CONTENT_VERSION,
    CURRENT_RULESET_VERSION,
    CURRENT_SEASON_ID,
    MIN_PASS_RATIO,
    validate_country_codes,
    validate_stage_identity,
)
from utils.country_regions import COUNTRY_REGION, REGION_COUNTRY_CODES, canonical_country_region

from datetime import timedelta
from math import ceil
from uuid import uuid4

router = APIRouter(prefix="/career", tags=["career"])


def validate_ranked_progression(
    db: Session,
    user_id: int,
    route_position: int,
    content_stage_id: int,
) -> None:
    """Bloquea saltos y fija el mapa ruta-contenido de la temporada."""
    existing = db.query(StageBest.route_position, StageBest.stage_id).filter(
        StageBest.user_id == user_id,
        StageBest.season_id == CURRENT_SEASON_ID,
    ).all()
    positions = {row.route_position for row in existing if row.route_position is not None}
    mapping = {row.route_position: int(row.stage_id) for row in existing if row.route_position is not None}
    reverse_mapping = {int(row.stage_id): row.route_position for row in existing if row.route_position is not None}

    if route_position > 1 and route_position - 1 not in positions:
        raise HTTPException(status_code=409, detail="Complete the previous route stage before ranking this one")
    if route_position in mapping and mapping[route_position] != content_stage_id:
        raise HTTPException(status_code=409, detail="Route position is already linked to another content stage")
    if content_stage_id in reverse_mapping and reverse_mapping[content_stage_id] != route_position:
        raise HTTPException(status_code=409, detail="Content stage is already linked to another route position")


@router.put("/profile", response_model=RankingProfileResponse)
async def update_ranking_profile(
    profile: RankingProfileUpdate,
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Actualiza sólo los datos públicos del ranking, no el nombre real."""
    from db.models import User

    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    season_profile = db.query(CareerSeasonProfile).filter(
        CareerSeasonProfile.user_id == current_user.id,
        CareerSeasonProfile.season_id == CURRENT_SEASON_ID,
    ).first()
    requested_country = user.country
    requested_region = user.ranking_region
    if profile.region is not None and profile.country is None:
        raise HTTPException(status_code=422, detail="Ranking region is derived from country")
    if profile.country is not None:
        try:
            requested_country, requested_region = canonical_country_region(profile.country)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if profile.region is not None and profile.region != requested_region:
            raise HTTPException(
                status_code=422,
                detail="Ranking region does not match country",
            )
    if season_profile and (
        requested_country != season_profile.country or requested_region != season_profile.region
    ):
        raise HTTPException(
            status_code=409,
            detail="Ranking country and region are locked for the active season",
        )

    if profile.display_name is not None:
        alias = profile.display_name.strip()
        user.ranking_alias = alias or None
    if profile.country is not None:
        user.country = requested_country
        user.ranking_region = requested_region

    db.commit()
    db.refresh(user)
    return {
        "display_name": user.ranking_alias,
        "country": user.country,
        "region": user.ranking_region,
        "ranked_profile_ready": bool(user.country and user.ranking_region),
        "ranking_origin_locked": season_profile is not None,
        "season_id": CURRENT_SEASON_ID,
    }


@router.post("/attempts", response_model=CareerAttemptResponse, status_code=201)
@limiter.limit("20/minute")
async def create_ranked_attempt(
    request: Request,
    payload: CareerAttemptCreate,
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Emite un intento de un solo uso antes de iniciar una partida clasificatoria."""
    from db.models import User
    from utils.career_scoring import get_difficulty_config

    if (
        payload.season_id != CURRENT_SEASON_ID
        or payload.ruleset_version != CURRENT_RULESET_VERSION
        or payload.content_version != CURRENT_CONTENT_VERSION
    ):
        raise HTTPException(status_code=409, detail="Ranking version is not active; update the app")

    try:
        validate_stage_identity(payload.route_position, payload.content_stage_id)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    config = get_difficulty_config(payload.difficulty)
    if len(payload.country_codes) != config['flags_total']:
        raise HTTPException(status_code=422, detail=f"{payload.difficulty} requires {config['flags_total']} countries")

    user = db.query(User).filter(User.id == current_user.id).first()
    if not user or not user.country or not user.ranking_region:
        raise HTTPException(status_code=409, detail="Complete the ranking profile before starting")
    try:
        country_codes = validate_country_codes(payload.content_stage_id, payload.country_codes, user.country)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    validate_ranked_progression(db, user.id, payload.route_position, payload.content_stage_id)

    season_profile = db.query(CareerSeasonProfile).filter(
        CareerSeasonProfile.user_id == user.id,
        CareerSeasonProfile.season_id == CURRENT_SEASON_ID,
    ).first()
    if season_profile and (
        season_profile.country != user.country or season_profile.region != user.ranking_region
    ):
        raise HTTPException(status_code=409, detail="Ranking origin is locked for the active season")
    if not season_profile:
        season_profile = CareerSeasonProfile(
            user_id=user.id,
            season_id=CURRENT_SEASON_ID,
            country=user.country,
            region=user.ranking_region,
        )
        db.add(season_profile)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attempt = CareerAttempt(
        id=str(uuid4()),
        user_id=user.id,
        season_id=CURRENT_SEASON_ID,
        ruleset_version=CURRENT_RULESET_VERSION,
        content_version=CURRENT_CONTENT_VERSION,
        stage_id=str(payload.content_stage_id),
        route_position=payload.route_position,
        difficulty=payload.difficulty,
        country_codes=country_codes,
        app_version=payload.app_version,
        started_at=now,
        expires_at=now + timedelta(seconds=ATTEMPT_TTL_SECONDS),
    )
    db.add(attempt)
    db.commit()
    return {
        "attempt_id": attempt.id,
        "season_id": attempt.season_id,
        "ruleset_version": attempt.ruleset_version,
        "content_version": attempt.content_version,
        "expires_at": attempt.expires_at,
    }


@router.post("/stages/{stage_id}/complete", status_code=200)
@limiter.limit("30/minute")
async def complete_stage(
    request: Request,
    stage_id: str,
    stage_data: StageCompleteRequest,
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)]
):
    """
    Registra la finalización de una etapa en modo carrera.

    - Valida score y tiempo.
    - Guarda en StageRun (historial siempre).
    - Actualiza StageBest (solo si es mejor).
    - Recalcula CareerUserStats (totales).
    """
    # 1. Validaciones básicas y vinculación al intento emitido.
    if stage_data.game_mode != "career":
        raise HTTPException(status_code=422, detail="Only career mode is supported")

    if stage_id != stage_data.stage_id:
        raise HTTPException(status_code=400, detail="stage_id mismatch between path and body")

    try:
        numeric_stage_id = int(stage_id)
        validate_stage_identity(stage_data.route_position, numeric_stage_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error) or "invalid stage_id") from error

    attempt = db.query(CareerAttempt).filter(
        CareerAttempt.id == stage_data.attempt_id,
        CareerAttempt.user_id == current_user.id,
    ).first()
    if not attempt:
        raise HTTPException(status_code=404, detail="Ranked attempt not found")
    if attempt.completed_at is not None:
        raise HTTPException(status_code=409, detail="Ranked attempt was already completed")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if now > attempt.expires_at:
        raise HTTPException(status_code=410, detail="Ranked attempt expired")
    expected_contract = (
        attempt.season_id,
        attempt.ruleset_version,
        attempt.content_version,
        attempt.stage_id,
        attempt.route_position,
        attempt.difficulty,
    )
    received_contract = (
        stage_data.season_id,
        stage_data.ruleset_version,
        stage_data.content_version,
        stage_data.stage_id,
        stage_data.route_position,
        stage_data.difficulty,
    )
    if received_contract != expected_contract:
        raise HTTPException(status_code=409, detail="Submission does not match the server-issued attempt")
    validate_ranked_progression(db, current_user.id, stage_data.route_position, numeric_stage_id)

    from utils.career_score_validation import authoritative_answers, validate_stage_score
    raw_submission = stage_data.model_dump(mode='json')
    validate_stage_score(stage_data, list(attempt.country_codes))
    answer_payload = authoritative_answers(stage_data)
    for answer_model, authoritative in zip(stage_data.answers, answer_payload):
        answer_model.correct = authoritative['correct']
        answer_model.wrong_attempts = authoritative['wrong_attempts']

    # El reloj del servidor es autoritario. Una entrega tardía obtiene cero
    # bonus temporal, pero sigue quedando auditada dentro del TTL del intento.
    from utils.career_scoring import get_difficulty_config
    elapsed_seconds = max(0, ceil((now - attempt.started_at).total_seconds()))
    stage_data.time_seconds = min(elapsed_seconds, get_difficulty_config(stage_data.difficulty)['time_limit'])

    from utils.career_scoring import calculate_score
    score_breakdown = calculate_score(answer_payload, stage_data.time_seconds, stage_data.difficulty)
    computed_score = score_breakdown['score']
    stage_data.hints_used = score_breakdown['hints_used']

    # El servidor es autoritario: sobreescribimos el score del request
    stage_data.score = computed_score
    correct_answers = sum(1 for answer in answer_payload if answer['correct'])
    passed = correct_answers / len(answer_payload) >= MIN_PASS_RATIO
    attempt.completed_at = now
    attempt.raw_submission = raw_submission

    from repository import career_repo
    try:
        # 2. Guardar historial (StageRun)
        run = career_repo.create_stage_run(db, current_user.id, stage_data, attempt, passed, answer_payload)

        # 3. Sólo una etapa aprobada puede crear/mejorar una marca clasificatoria.
        best = None
        is_better = False
        if passed:
            best, is_better = career_repo.upsert_stage_best_if_better(db, current_user.id, stage_id, stage_data)

        # 4. Recalcular sólo el alcance de esta temporada y dificultad.
        stats = career_repo.recompute_career_stats_from_best(
            db, current_user.id, stage_data.season_id, stage_data.difficulty
        )

        db.commit()

        return {
            "stage_run_id": run.id,
            "ranked": passed,
            "correct_answers": correct_answers,
            "score": computed_score,
            "base_score": score_breakdown['base_score'],
            "time_bonus": score_breakdown['time_bonus'],
            "clean_bonus": score_breakdown['clean_bonus'],
            "hints_used": score_breakdown['hints_used'],
            "mistakes": score_breakdown['mistakes'],
            "stage_best_updated": is_better,
            "stage_best": {
                "score": best.score,
                "hints_used": best.hints_used,
                "mistakes": best.mistakes,
                "difficulty": best.difficulty,
                "time_seconds": best.time_seconds,
                "achieved_at": best.achieved_at
            } if best else None,
            "career_stats": {
                "stages_completed": stats.stages_completed,
                "total_score": stats.total_score,
                "total_hints_used": stats.total_hints_used,
                "total_time_seconds": stats.total_time_seconds,
                "total_mistakes": stats.total_mistakes,
            }
        }
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ranked attempt or stage result was already recorded") from e
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("uvicorn.error").error(f"Error persisting career stage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error de persistencia interna")


@router.get("/me", response_model=CareerStatsResponse)
async def get_my_career_profile(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
    difficulty: Literal['easy', 'normal', 'hard'] = 'normal',
):
    """
    Obtiene el perfil de carrera del usuario actual (stats + rank).
    """
    from sqlalchemy import func

    scoped_stats = db.query(
        StageBest.user_id.label("user_id"),
        func.count(StageBest.id).label("stages_completed"),
        func.sum(StageBest.score).label("total_score"),
        func.sum(StageBest.hints_used).label("total_hints_used"),
        func.sum(StageBest.mistakes).label("total_mistakes"),
        func.sum(StageBest.time_seconds).label("total_time_seconds"),
    ).filter(
        StageBest.season_id == CURRENT_SEASON_ID,
        StageBest.ruleset_version == CURRENT_RULESET_VERSION,
        StageBest.content_version == CURRENT_CONTENT_VERSION,
        StageBest.difficulty == difficulty,
    ).group_by(StageBest.user_id).subquery()

    stats = db.query(scoped_stats).filter(scoped_stats.c.user_id == current_user.id).first()
    if stats is None:
        return CareerStatsResponse(
            stages_completed=0,
            total_score=0,
            total_hints_used=0,
            total_time_seconds=0,
            rank=None
        )

    better_count = db.query(func.count(scoped_stats.c.user_id)).filter(
        (scoped_stats.c.stages_completed > stats.stages_completed) |
        ((scoped_stats.c.stages_completed == stats.stages_completed) &
         (scoped_stats.c.total_score > stats.total_score)) |
        ((scoped_stats.c.stages_completed == stats.stages_completed) &
         (scoped_stats.c.total_score == stats.total_score) &
         (scoped_stats.c.total_hints_used < stats.total_hints_used)) |
        ((scoped_stats.c.stages_completed == stats.stages_completed) &
         (scoped_stats.c.total_score == stats.total_score) &
         (scoped_stats.c.total_hints_used == stats.total_hints_used) &
         (scoped_stats.c.total_mistakes < stats.total_mistakes)) |
        ((scoped_stats.c.stages_completed == stats.stages_completed) &
         (scoped_stats.c.total_score == stats.total_score) &
         (scoped_stats.c.total_hints_used == stats.total_hints_used) &
         (scoped_stats.c.total_mistakes == stats.total_mistakes) &
         (scoped_stats.c.total_time_seconds < stats.total_time_seconds))
    ).scalar()

    return CareerStatsResponse(
        stages_completed=stats.stages_completed,
        total_score=stats.total_score,
        total_hints_used=stats.total_hints_used,
        total_time_seconds=stats.total_time_seconds,
        total_mistakes=stats.total_mistakes,
        rank=better_count + 1
    )


@router.get("/me/stats", response_model=CareerMeStatsResponse)
async def get_my_career_stats_screen(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
    difficulty: Literal['easy', 'normal', 'hard'] = 'normal',
):
    """
    Obtiene las estadísticas detalladas para la pantalla 'Mis Estadísticas'.
    """
    from repository import career_repo
    return career_repo.get_me_stats(db, current_user.id, CURRENT_SEASON_ID, difficulty)


@router.get("/me/history", response_model=CareerRunHistoryResponse)
async def get_my_career_history(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
    difficulty: Literal['easy', 'normal', 'hard'] = 'normal',
    limit: int = Query(default=50, ge=1, le=100),
):
    """Historial cronológico de intentos de Viaje para la evolución personal."""
    from repository import career_repo
    return career_repo.get_run_history(db, current_user.id, CURRENT_SEASON_ID, difficulty, limit)


@router.get("/leaderboard", response_model=CareerLeaderboardResponse)
async def get_career_leaderboard(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    country: Optional[str] = None,
    region: Optional[Literal['Americas', 'Europe', 'Asia', 'Africa', 'Oceania']] = None,
    difficulty: Literal['easy', 'normal', 'hard'] = 'normal',
    db: Session = Depends(get_db)
):
    """
    Ranking de modo carrera ordenado por:
    1. stages_completed DESC
    2. total_score DESC
    3. total_hints_used ASC
    4. total_mistakes ASC
    5. total_time_seconds ASC
    """
    from db.models import User
    from sqlalchemy import func

    # Cada dificultad tiene su propio ranking: 12 banderas no compiten contra 8.
    scoped_stats = db.query(
        StageBest.user_id.label("user_id"),
        func.count(StageBest.id).label("stages_completed"),
        func.sum(StageBest.score).label("total_score"),
        func.sum(StageBest.hints_used).label("total_hints_used"),
        func.sum(StageBest.mistakes).label("total_mistakes"),
        func.sum(StageBest.time_seconds).label("total_time_seconds"),
        func.max(StageBest.achieved_at).label("last_activity_at"),
    ).filter(
        StageBest.difficulty == difficulty,
        StageBest.season_id == CURRENT_SEASON_ID,
        StageBest.ruleset_version == CURRENT_RULESET_VERSION,
        StageBest.content_version == CURRENT_CONTENT_VERSION,
    ).group_by(StageBest.user_id).subquery()

    ranking_order = (
        scoped_stats.c.stages_completed.desc(),
        scoped_stats.c.total_score.desc(),
        scoped_stats.c.total_hints_used.asc(),
        scoped_stats.c.total_mistakes.asc(),
        scoped_stats.c.total_time_seconds.asc(),
        scoped_stats.c.last_activity_at.asc(),
    )
    rank = func.rank().over(order_by=ranking_order).label("rank")
    query = db.query(
        rank,
        scoped_stats.c.user_id,
        User.username,
        User.ranking_alias,
        CareerSeasonProfile.country,
        CareerSeasonProfile.region,
        scoped_stats.c.stages_completed,
        scoped_stats.c.total_score,
        scoped_stats.c.total_hints_used,
        scoped_stats.c.total_mistakes,
        scoped_stats.c.total_time_seconds,
        scoped_stats.c.last_activity_at,
    ).join(User, User.id == scoped_stats.c.user_id).join(
        CareerSeasonProfile,
        (CareerSeasonProfile.user_id == scoped_stats.c.user_id)
        & (CareerSeasonProfile.season_id == CURRENT_SEASON_ID),
    )

    if country:
        query = query.filter(func.upper(CareerSeasonProfile.country) == country.upper())
    if region:
        query = query.filter(
            func.upper(CareerSeasonProfile.country).in_(REGION_COUNTRY_CODES[region])
        )

    # Get total count for the filtered query
    total = db.query(func.count(scoped_stats.c.user_id)).select_from(scoped_stats).join(
        CareerSeasonProfile,
        (CareerSeasonProfile.user_id == scoped_stats.c.user_id)
        & (CareerSeasonProfile.season_id == CURRENT_SEASON_ID),
    )
    if country:
        total = total.filter(func.upper(CareerSeasonProfile.country) == country.upper())
    if region:
        total = total.filter(
            func.upper(CareerSeasonProfile.country).in_(REGION_COUNTRY_CODES[region])
        )
    total_count = total.scalar()

    results = query.order_by(
        *ranking_order
    ).limit(limit).offset(offset).all()

    return {
        "items": [
            {
                "rank": r[0],
                "user_id": r[1],
                "username": r[2],
                "display_name": r[3] or r[2],
                "country": r[4].upper(),
                "region": COUNTRY_REGION.get(r[4].upper(), r[5]),
                "difficulty": difficulty,
                "avatar_url": f"/user/{r[1]}/profile_image",
                "stages_completed": r[6],
                "total_score": r[7],
                "total_hints_used": r[8],
                "total_mistakes": r[9],
                "total_time_seconds": r[10],
                "last_activity_at": r[11]
            }
            for i, r in enumerate(results)
        ],
        "total": total_count,
        "updated_at": datetime.now(timezone.utc),
        "season_id": CURRENT_SEASON_ID,
        "ruleset_version": CURRENT_RULESET_VERSION,
        "content_version": CURRENT_CONTENT_VERSION,
    }
