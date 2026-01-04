from pydantic import BaseModel
from enum import Enum
from datetime import datetime
from typing import Optional

class ScoreScope(str, Enum):
    global_scope = "global"
    user = "user"
    country = "country"
    region = "region"

class RegionEnum(str, Enum):
    career = "career"
    america = "america"
    europe = "europe"
    asia = "asia"
    africa = "africa"
    oceania = "oceania"

class ScoreRequest(BaseModel):
    score: int
    game_duration_seconds: Optional[int] = None
    game_mode: Optional[str] = None
    game_region: Optional[str] = None # Input from frontend (URL or key)

class ScoreResponse(BaseModel):
    id: int
    user_id: int
    score: int
    created_at: datetime
    
    class Config:
        from_attributes = True

class ScorePublic(BaseModel):
    rank: int
    username: str
    score: int
    country: Optional[str] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# =============================================================================
# CAREER MODE SCHEMAS
# Solo para modo carrera. Requieren game_mode explícito.
# =============================================================================

class StageCompleteRequest(BaseModel):
    """Request para completar una etapa en modo carrera."""
    stage_id: str
    score: int
    hints_used: int = 0
    time_seconds: int  # Mapeado desde seconds_used en el frontend
    time_limit: Optional[int] = None  # Límite de tiempo de la etapa para bonus
    flags_total: Optional[int] = None  # Total de banderas en la etapa
    groups: Optional[list[dict]] = None  # Detalles de los grupos (Array(7))
    completed_at: Optional[datetime] = None  # Si viene, se usa como created_at
    game_mode: str  # Requerido, debe ser "career"


class CareerStatsResponse(BaseModel):
    """Stats agregados del usuario en modo carrera."""
    stages_completed: int
    total_score: int
    total_hints_used: int
    total_time_seconds: int
    rank: Optional[int] = None
    
    class Config:
        from_attributes = True