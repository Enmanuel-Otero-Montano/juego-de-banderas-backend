# -*- coding: utf-8 -*-
"""
Career Mode Router

Endpoints exclusivos para modo carrera.
Requieren game_mode == "career" explícito, NO aceptan inferencia.

El frontend detecta el modo por URL (career-mode), pero el backend NO confía
y valida game_mode explícitamente en cada request.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import Annotated, Optional
from datetime import datetime

from schemas import user_schema
from schemas.score import StageCompleteRequest, CareerStatsResponse
from db.models import CareerUserStats, StageBest, StageRun
from dependencies import get_current_active_user, get_db
from utils.career_gating import require_career_mode

router = APIRouter(prefix="/career", tags=["career"])


@router.post("/stages/{stage_id}/complete", status_code=200)
async def complete_stage(
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
    # 1. Validaciones básicas
    try:
        require_career_mode(stage_data.game_mode)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    if stage_id != stage_data.stage_id:
        raise HTTPException(status_code=400, detail="stage_id mismatch between path and body")
        
    from utils.career_score_validation import validate_stage_score
    validate_stage_score(stage_data)

    from utils.career_scoring import compute_stage_score
    computed_score = compute_stage_score(stage_id, stage_data.groups, stage_data.hints_used, stage_data.time_seconds)
    
    # Log mismatch if client sent a different score
    if stage_data.score != computed_score:
        import logging
        logging.getLogger("uvicorn.error").warning(
            f"Score mismatch for user {current_user.id} on stage {stage_id}: "
            f"Client sent {stage_data.score}, Server computed {computed_score}. Using Server score."
        )
    
    # El servidor es autoritario: sobreescribimos el score del request
    stage_data.score = computed_score

    from repository import career_repo
    try:
        # 2. Guardar historial (StageRun)
        run = career_repo.create_stage_run(db, current_user.id, stage_data)
        
        # 3. Actualizar mejor resultado (StageBest)
        best, is_better = career_repo.upsert_stage_best_if_better(db, current_user.id, stage_id, stage_data)
        
        # 4. Recalcular estadísticas totales (CareerUserStats) desde StageBest
        stats = career_repo.recompute_career_stats_from_best(db, current_user.id)
        
        db.commit()
        
        return {
            "stage_run_id": run.id,
            "stage_best_updated": is_better,
            "stage_best": {
                "score": best.score,
                "hints_used": best.hints_used,
                "time_seconds": best.time_seconds,
                "achieved_at": best.achieved_at
            },
            "career_stats": {
                "stages_completed": stats.stages_completed,
                "total_score": stats.total_score,
                "total_hints_used": stats.total_hints_used,
                "total_time_seconds": stats.total_time_seconds
            }
        }
    except Exception as e:
        db.rollback()
        import logging
        logging.getLogger("uvicorn.error").error(f"Error persisting career stage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error de persistencia interna")


@router.get("/me/stats", response_model=CareerStatsResponse)
async def get_my_career_stats(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)]
):
    """
    Obtiene las estadísticas de carrera del usuario actual.
    """
    stats = db.query(CareerUserStats).filter(
        CareerUserStats.user_id == current_user.id
    ).first()
    
    if not stats:
        return CareerStatsResponse(
            stages_completed=0,
            total_score=0,
            total_hints_used=0,
            total_time_seconds=0,
            rank=None
        )
    
    # Calcular rank (posición en el leaderboard)
    # Cuenta cuántos tienen mejor posición
    from sqlalchemy import func
    better_count = db.query(func.count(CareerUserStats.user_id)).filter(
        (CareerUserStats.stages_completed > stats.stages_completed) |
        ((CareerUserStats.stages_completed == stats.stages_completed) & 
         (CareerUserStats.total_score > stats.total_score)) |
        ((CareerUserStats.stages_completed == stats.stages_completed) & 
         (CareerUserStats.total_score == stats.total_score) &
         (CareerUserStats.total_hints_used < stats.total_hints_used)) |
        ((CareerUserStats.stages_completed == stats.stages_completed) & 
         (CareerUserStats.total_score == stats.total_score) &
         (CareerUserStats.total_hints_used == stats.total_hints_used) &
         (CareerUserStats.total_time_seconds < stats.total_time_seconds))
    ).scalar()
    
    return CareerStatsResponse(
        stages_completed=stats.stages_completed,
        total_score=stats.total_score,
        total_hints_used=stats.total_hints_used,
        total_time_seconds=stats.total_time_seconds,
        rank=better_count + 1
    )


@router.get("/leaderboard")
async def get_career_leaderboard(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db)
):
    """
    Ranking de modo carrera ordenado por:
    1. stages_completed DESC
    2. total_score DESC
    3. total_hints_used ASC
    4. total_time_seconds ASC
    """
    from db.models import User
    
    results = db.query(
        CareerUserStats.user_id,
        User.username,
        User.country,
        CareerUserStats.stages_completed,
        CareerUserStats.total_score,
        CareerUserStats.total_hints_used,
        CareerUserStats.total_time_seconds,
        CareerUserStats.last_activity_at
    ).join(User, User.id == CareerUserStats.user_id).order_by(
        CareerUserStats.stages_completed.desc(),
        CareerUserStats.total_score.desc(),
        CareerUserStats.total_hints_used.asc(),
        CareerUserStats.total_time_seconds.asc(),
        CareerUserStats.last_activity_at.asc()
    ).limit(limit).offset(offset).all()
    
    return [
        {
            "rank": offset + i + 1,
            "user_id": r[0],
            "username": r[1],
            "country": r[2],
            "stages_completed": r[3],
            "total_score": r[4],
            "total_hints_used": r[5],
            "total_time_seconds": r[6],
            "last_activity_at": r[7]
        }
        for i, r in enumerate(results)
    ]
