from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Annotated, Optional

from schemas import user_schema, score
from repository import scores_repo
from dependencies import get_current_active_user, get_db  # <-- Cambio aquí

router = APIRouter(prefix="/scores", tags=["scores"])

@router.post("/")
async def save_score(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    score_data: user_schema.ScoreRequest,
    db: Annotated[Session, Depends(get_db)]
):
    """Guarda un nuevo score del usuario"""
    result = scores_repo.save_score(db, score_data.score, current_user)
    return result

@router.get("/me/best")
async def get_my_best_score(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Session = Depends(get_db)
):
    """Obtiene la mejor puntuación histórica del usuario actual"""
    record = scores_repo.get_user_best_score(db, current_user.id)
    if not record:
        return {"max_score": 0}
    return {"max_score": record.max_score}


@router.get("/")
async def get_scores(
    scope: score.ScoreScope = Query(default=score.ScoreScope.global_scope),
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user_id: Optional[int] = None,
    country_code: Optional[str] = None,
    region: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Obtiene rankings según el scope especificado"""
    if scope == score.ScoreScope.global_scope:
        return scores_repo.get_public_ranking(db, limit, offset)
    
    elif scope == score.ScoreScope.user:
        if not user_id:
            raise HTTPException(400, "user_id required for user scope")
        return scores_repo.get_user_scores(db, user_id, limit, offset)
    
    elif scope == score.ScoreScope.country:
        if not country_code:
            raise HTTPException(400, "country_code required for country scope")
        return scores_repo.get_country_scores(db, country_code, limit, offset)
    
    elif scope == score.ScoreScope.region:
        if not region:
            raise HTTPException(400, "region required for region scope")
        return scores_repo.get_region_scores(db, region, limit, offset)