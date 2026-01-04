# -*- coding: utf-8 -*-
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
from db.models import CareerUserStats, StageBest, StageRun
from schemas.score import StageCompleteRequest

def create_stage_run(db: Session, user_id: int, stage_data: StageCompleteRequest) -> StageRun:
    """
    SIEMPRE guarda una fila en StageRun por cada finalización de etapa.
    """
    run = StageRun(
        user_id=user_id,
        stage_id=stage_data.stage_id,
        score=stage_data.score,
        hints_used=stage_data.hints_used,
        time_seconds=stage_data.time_seconds,
        groups=stage_data.groups,
        created_at=stage_data.completed_at or datetime.utcnow()
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
    3) Si hints empata: A.time_seconds < B.time_seconds
    """
    existing_best = db.query(StageBest).filter(
        StageBest.user_id == user_id,
        StageBest.stage_id == stage_id
    ).first()

    is_better = False
    if not existing_best:
        is_better = True
        best = StageBest(
            user_id=user_id,
            stage_id=stage_id,
            score=run_data.score,
            hints_used=run_data.hints_used,
            time_seconds=run_data.time_seconds,
            groups=run_data.groups,
            achieved_at=run_data.completed_at or datetime.utcnow()
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
                if run_data.time_seconds < existing_best.time_seconds:
                    is_better = True
        
        if is_better:
            best.score = run_data.score
            best.hints_used = run_data.hints_used
            best.time_seconds = run_data.time_seconds
            best.groups = run_data.groups
            best.achieved_at = run_data.completed_at or datetime.utcnow()

    db.flush()
    return best, is_better

def recompute_career_stats_from_best(db: Session, user_id: int) -> CareerUserStats:
    """
    Recalcula/actualiza CareerUserStats para ranking desde la tabla StageBest.
    """
    # Obtener agregados de StageBest para este usuario
    stats_query = db.query(
        func.count(StageBest.id).label("stages_completed"),
        func.sum(StageBest.score).label("total_score"),
        func.sum(StageBest.hints_used).label("total_hints_used"),
        func.sum(StageBest.time_seconds).label("total_time_seconds")
    ).filter(StageBest.user_id == user_id).first()

    # Obtener la última actividad (el max achieved_at de sus mejores intentos o corridas)
    # Por consistencia con ranking, usamos el último cambio en un "best"
    last_activity = db.query(func.max(StageBest.achieved_at)).filter(StageBest.user_id == user_id).scalar()

    stats = db.query(CareerUserStats).filter(CareerUserStats.user_id == user_id).first()
    
    if not stats:
        stats = CareerUserStats(user_id=user_id)
        db.add(stats)

    stats.stages_completed = stats_query.stages_completed or 0
    stats.total_score = int(stats_query.total_score or 0)
    stats.total_hints_used = int(stats_query.total_hints_used or 0)
    stats.total_time_seconds = int(stats_query.total_time_seconds or 0)
    stats.last_activity_at = last_activity or datetime.utcnow()
    
    db.flush()
    return stats
