# -*- coding: utf-8 -*-
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timezone
from db.models import CareerAttempt, CareerUserStats, StageBest, StageRun
from schemas.score import StageCompleteRequest


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mistakes(stage_data: StageCompleteRequest) -> int:
    return sum(answer.wrong_attempts for answer in stage_data.answers)

def create_stage_run(
    db: Session,
    user_id: int,
    stage_data: StageCompleteRequest,
    attempt: CareerAttempt,
    passed: bool,
    answers: list[dict],
) -> StageRun:
    """
    SIEMPRE guarda una fila en StageRun por cada finalización de etapa.
    """
    run = StageRun(
        user_id=user_id,
        stage_id=stage_data.stage_id,
        route_position=stage_data.route_position,
        season_id=stage_data.season_id,
        ruleset_version=stage_data.ruleset_version,
        content_version=stage_data.content_version,
        attempt_id=attempt.id,
        passed=passed,
        correct_answers=sum(1 for answer in answers if answer['correct']),
        score=stage_data.score,
        hints_used=stage_data.hints_used,
        mistakes=_mistakes(stage_data),
        difficulty=stage_data.difficulty,
        time_seconds=stage_data.time_seconds,
        groups=stage_data.groups,
        answers=answers,
        created_at=utc_now(),
    )
    db.add(run)
    db.flush()  # Para obtener el ID si fuera necesario antes del commit
    return run

def upsert_stage_best_if_better(db: Session, user_id: int, stage_id: str, run_data: StageCompleteRequest) -> tuple[StageBest, bool]:
    """
    Actualiza StageBest SOLO si esta corrida es “mejor”.
    Reglas:
    1) A.score > B.score
    2) Si score empata: A.hints_used < B.hints_used
    3) Si hints empata: A.mistakes < B.mistakes
    4) Si también empata: A.time_seconds < B.time_seconds
    """
    existing_best = db.query(StageBest).filter(
        StageBest.user_id == user_id,
        StageBest.stage_id == stage_id,
        StageBest.difficulty == run_data.difficulty,
        StageBest.season_id == run_data.season_id,
    ).first()

    is_better = False
    if not existing_best:
        is_better = True
        best = StageBest(
            user_id=user_id,
            stage_id=stage_id,
            route_position=run_data.route_position,
            season_id=run_data.season_id,
            ruleset_version=run_data.ruleset_version,
            content_version=run_data.content_version,
            score=run_data.score,
            hints_used=run_data.hints_used,
            mistakes=_mistakes(run_data),
            difficulty=run_data.difficulty,
            time_seconds=run_data.time_seconds,
            groups=run_data.groups,
            achieved_at=utc_now(),
        )
        db.add(best)
    else:
        best = existing_best
        # Comparación lógica
        if run_data.score > existing_best.score:
            is_better = True
        elif run_data.score == existing_best.score:
            if run_data.hints_used < existing_best.hints_used:
                is_better = True
            elif run_data.hints_used == existing_best.hints_used:
                if _mistakes(run_data) < existing_best.mistakes:
                    is_better = True
                elif _mistakes(run_data) == existing_best.mistakes and run_data.time_seconds < existing_best.time_seconds:
                    is_better = True

        if is_better:
            best.score = run_data.score
            best.hints_used = run_data.hints_used
            best.mistakes = _mistakes(run_data)
            best.time_seconds = run_data.time_seconds
            best.route_position = run_data.route_position
            best.ruleset_version = run_data.ruleset_version
            best.content_version = run_data.content_version
            best.groups = run_data.groups
            best.achieved_at = utc_now()

    db.flush()
    return best, is_better

def recompute_career_stats_from_best(db: Session, user_id: int, season_id: str, difficulty: str) -> CareerUserStats:
    """
    Recalcula/actualiza CareerUserStats para ranking desde la tabla StageBest.
    """
    # Obtener agregados de StageBest para este usuario
    stats_query = db.query(
        func.count(StageBest.id).label("stages_completed"),
        func.sum(StageBest.score).label("total_score"),
        func.sum(StageBest.hints_used).label("total_hints_used"),
        func.sum(StageBest.time_seconds).label("total_time_seconds"),
        func.sum(StageBest.mistakes).label("total_mistakes")
    ).filter(
        StageBest.user_id == user_id,
        StageBest.season_id == season_id,
        StageBest.difficulty == difficulty,
    ).first()

    # Obtener la última actividad (el max achieved_at de sus mejores intentos o corridas)
    # Por consistencia con ranking, usamos el último cambio en un "best"
    last_activity = db.query(func.max(StageBest.achieved_at)).filter(
        StageBest.user_id == user_id,
        StageBest.season_id == season_id,
        StageBest.difficulty == difficulty,
    ).scalar()

    stats = db.query(CareerUserStats).filter(CareerUserStats.user_id == user_id).first()

    if not stats:
        stats = CareerUserStats(user_id=user_id)
        db.add(stats)

    stats.stages_completed = stats_query.stages_completed or 0
    stats.total_score = int(stats_query.total_score or 0)
    stats.total_hints_used = int(stats_query.total_hints_used or 0)
    stats.total_time_seconds = int(stats_query.total_time_seconds or 0)
    stats.total_mistakes = int(stats_query.total_mistakes or 0)
    stats.last_activity_at = last_activity or utc_now()

    db.flush()
    return stats

def get_me_stats(db: Session, user_id: int, season_id: str, difficulty: str) -> dict:
    """
    Obtiene métricas agregadas para la pantalla 'Mis Estadísticas'.
    """

    # 1. highest_stage_reached & max_score
    stats_query = db.query(
        func.max(StageBest.route_position).label("highest_stage"),
        func.max(StageBest.score).label("max_score")
    ).filter(
        StageBest.user_id == user_id,
        StageBest.season_id == season_id,
        StageBest.difficulty == difficulty,
    ).first()

    # 2. last_played
    last_run = db.query(StageRun).filter(
        StageRun.user_id == user_id,
        StageRun.season_id == season_id,
        StageRun.difficulty == difficulty,
    ).order_by(StageRun.created_at.desc()).first()

    # 3. Posición dentro de la misma temporada y dificultad.
    rank = None
    scoped = db.query(
        StageBest.user_id.label('user_id'),
        func.count(StageBest.id).label('stages_completed'),
        func.sum(StageBest.score).label('total_score'),
        func.sum(StageBest.hints_used).label('total_hints_used'),
        func.sum(StageBest.mistakes).label('total_mistakes'),
        func.sum(StageBest.time_seconds).label('total_time_seconds'),
    ).filter(
        StageBest.season_id == season_id,
        StageBest.difficulty == difficulty,
    ).group_by(StageBest.user_id).subquery()
    user_stats = db.query(scoped).filter(scoped.c.user_id == user_id).first()
    if user_stats:
        better_count = db.query(func.count(scoped.c.user_id)).filter(
            (scoped.c.stages_completed > user_stats.stages_completed) |
            ((scoped.c.stages_completed == user_stats.stages_completed) &
             (scoped.c.total_score > user_stats.total_score)) |
            ((scoped.c.stages_completed == user_stats.stages_completed) &
             (scoped.c.total_score == user_stats.total_score) &
             (scoped.c.total_hints_used < user_stats.total_hints_used)) |
            ((scoped.c.stages_completed == user_stats.stages_completed) &
             (scoped.c.total_score == user_stats.total_score) &
             (scoped.c.total_hints_used == user_stats.total_hints_used) &
             (scoped.c.total_mistakes < user_stats.total_mistakes)) |
            ((scoped.c.stages_completed == user_stats.stages_completed) &
             (scoped.c.total_score == user_stats.total_score) &
             (scoped.c.total_hints_used == user_stats.total_hints_used) &
             (scoped.c.total_mistakes == user_stats.total_mistakes) &
             (scoped.c.total_time_seconds < user_stats.total_time_seconds))
        ).scalar()
        rank = better_count + 1

    return {
        "highest_stage_reached": stats_query.highest_stage or 0,
        "stages_total": 12,
        "max_score": stats_query.max_score or 0,
        "last_played": {
            "stage_id": last_run.stage_id,
            "played_at": last_run.created_at
        } if last_run else None,
        "leaderboard_rank": rank
    }
