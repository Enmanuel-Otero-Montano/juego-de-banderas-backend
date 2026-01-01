from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Annotated, Optional

from schemas import user_schema, score
from schemas.score import RegionEnum, ScoreScope
from repository import scores_repo
from dependencies import get_current_active_user, get_db  # <-- Cambio aquí

router = APIRouter(prefix="/scores", tags=["scores"])

def normalize_region(input_region: str | None) -> str:
    """
    Normalizes a region string (URL or key) to a valid RegionEnum key.
    Defaults to 'career' if invalid or None.
    """
    if not input_region:
        return "career"
    
    # Handle URLs (frontend sends location.href)
    # Example: https://banderas.com/game -> career
    # Example: https://banderas.com/game/america -> america
    
    # Simple check: does it look like a URL?
    if "http" in input_region or "/" in input_region:
        parts = input_region.rstrip("/").split("/")
        last_part = parts[-1].lower()
        
        # Check if last part is a valid region
        if last_part in RegionEnum.__members__:
            return last_part
        else:
            # If URL doesn't end in a known region, assume career (e.g. /game)
            return "career"
    
    # If it's just a string, check if it's a valid key
    if input_region.lower() in RegionEnum.__members__:
        return input_region.lower()
    
    return "career"

@router.post("/")
async def save_score(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    score_data: score.ScoreRequest,
    db: Annotated[Session, Depends(get_db)]
):
    """Guarda un nuevo score del usuario"""
    # 0. Determine Region
    region_key = normalize_region(score_data.game_region)
    
    # 1. Fetch user history for validation (for this specific region)
    user_history = scores_repo.get_user_best_score(db, current_user.id, region_key)
    
    # 2. Validate score
    from utils.score_validator import validate_score_legitimacy
    # Note: Validator warns/raises but returns True if valid
    validate_score_legitimacy(score_data.score, score_data, user_history)
    
    # 3. Save score
    # Use user's country from profile as the country_code for the score
    result = scores_repo.save_score(
        db=db, 
        score=score_data.score, 
        current_user=current_user,
        region_key=region_key,
        country_code=current_user.country
    )
    return result

@router.get("/me/best")
@router.get("/me/best")
async def get_my_best_score(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Session = Depends(get_db)
):
    """Obtiene la mejor puntuación histórica del usuario actual (Career)"""
    # Default to career for backward compatibility
    record = scores_repo.get_user_best_score(db, current_user.id, "career")
    if not record:
        return {"max_score": 0}
    return {"max_score": record.max_score}


@router.get("/me/position")
async def get_my_position(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    scope: ScoreScope = Query(default=ScoreScope.global_scope),
    region: Optional[str] = None, # For 'region' scope
    db: Session = Depends(get_db)
):
    """
    Obtiene la posición (rank) del usuario en un scope determinado.
    Ej: scope=global, scope=country (usa el del usuario), scope=region (requiere param region)
    """
    scope_value = None
    
    if scope == ScoreScope.region:
        if not region:
            # Try to infer or error? Use 'career' default or error?
            # Prompt says: "Si frontend no manda... inferirlo". 
            # But for explicit 'region' scope query, we probably want a value.
            # Let's default to career if not provided.
            scope_value = "career"
        else:
            scope_value = normalize_region(region)
            
    elif scope == ScoreScope.country:
        if not current_user.country:
             # If user has no country, maybe return 0 rank or error?
             # Let's return a clean structure indicating no country data.
             return {"scope": scope, "rank": None, "max_score": 0, "total_players": 0, "message": "User has no country set"}
        scope_value = current_user.country

    rank_data = scores_repo.get_user_rank(db, current_user.id, scope, scope_value)
    
    if not rank_data:
         # User has no score in this scope
         return {"scope": scope, "rank": None, "max_score": 0, "total_players": 0}
         
    return rank_data


@router.get("/summary")
async def get_scores_summary(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    limit: int = Query(default=10, le=50),
    db: Session = Depends(get_db)
):
    """
    Retorna un resumen completo para el perfil:
    - Top Global
    - Top Country (user's country)
    - User Positions
    - User Best
    """
    return scores_repo.get_summary(db, current_user)


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
        return scores_repo.get_user_scores_history(db, user_id, limit, offset)
    
    elif scope == score.ScoreScope.country:
        if not country_code:
            raise HTTPException(400, "country_code required for country scope")
        return scores_repo.get_country_scores(db, country_code, limit, offset)
    
    elif scope == score.ScoreScope.region:
        # Normalize region
        target_region = normalize_region(region)
        return scores_repo.get_region_scores(db, target_region, limit, offset)