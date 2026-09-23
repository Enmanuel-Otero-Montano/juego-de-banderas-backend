# =============================================================================
# CAREER MODE SCHEMAS
# Solo para modo carrera.
# =============================================================================


from pydantic import BaseModel, ConfigDict, Field, field_validator
from datetime import datetime
from typing import Literal, Optional


class FlagResult(BaseModel):
    """Resultado verificable de una bandera dentro de una etapa."""
    country_code: str = Field(min_length=2, max_length=2, pattern=r'^[A-Za-z]{2}$')
    selected_codes: list[str] = Field(default_factory=list, max_length=20)
    # Compatibilidad de lectura/auditoría. El servidor no confía en estos
    # campos y deriva aciertos/errores desde selected_codes.
    correct: Optional[bool] = None
    used_hint: bool = False
    wrong_attempts: int = Field(default=0, ge=0, le=20)

    @field_validator('selected_codes')
    @classmethod
    def validate_selected_codes(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        if any(len(value) != 2 or not value.isalpha() for value in normalized):
            raise ValueError('selected codes must use two letters')
        return normalized


class CareerAttemptCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')

    route_position: int = Field(ge=1, le=12)
    content_stage_id: int = Field(ge=1, le=12)
    difficulty: Literal['easy', 'normal', 'hard']
    country_codes: list[str] = Field(min_length=1, max_length=12)
    season_id: str
    ruleset_version: int
    content_version: int
    app_version: Optional[str] = Field(default=None, max_length=32)


class CareerAttemptResponse(BaseModel):
    attempt_id: str
    season_id: str
    ruleset_version: int
    content_version: int
    expires_at: datetime

class StageCompleteRequest(BaseModel):
    """Request para completar una etapa en modo carrera."""
    model_config = ConfigDict(extra='forbid')

    attempt_id: str = Field(min_length=32, max_length=64)
    stage_id: str
    route_position: int = Field(ge=1, le=12)
    season_id: str
    ruleset_version: int
    content_version: int
    # Kept temporarily for older clients. The API recomputes this value.
    score: int = 0
    hints_used: int = 0
    time_seconds: int  # Mapeado desde seconds_used en el frontend
    time_limit: Optional[int] = None  # Límite de tiempo de la etapa para bonus
    flags_total: Optional[int] = None  # Total de banderas en la etapa
    groups: Optional[list[dict]] = None  # Detalles de los grupos (Array(7))
    answers: list[FlagResult] = Field(default_factory=list)
    difficulty: Literal['easy', 'normal', 'hard'] = 'normal'
    game_mode: str  # Requerido, debe ser "career"


class CareerStatsResponse(BaseModel):
    """Stats agregados del usuario en modo carrera."""
    stages_completed: int
    total_score: int
    total_hints_used: int
    total_time_seconds: int
    total_mistakes: int = 0
    rank: Optional[int] = None
    
    model_config = ConfigDict(from_attributes=True)

class CareerLeaderboardEntry(BaseModel):
    rank: int
    user_id: int
    username: str
    display_name: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    difficulty: Literal['easy', 'normal', 'hard']
    avatar_url: Optional[str] = None
    stages_completed: int
    total_score: int
    total_hints_used: int
    total_time_seconds: int
    total_mistakes: int = 0
    last_activity_at: datetime

class CareerLeaderboardResponse(BaseModel):
    items: list[CareerLeaderboardEntry]
    total: int
    updated_at: datetime
    season_id: str
    ruleset_version: int
    content_version: int


class RankingProfileUpdate(BaseModel):
    """Datos voluntarios y públicos que se muestran en el ranking."""
    # Una cadena vacía permite volver al nombre de usuario por defecto.
    display_name: Optional[str] = Field(default=None, max_length=24)
    country: Optional[str] = Field(default=None, min_length=2, max_length=2)
    # Compatibilidad temporal: clientes anteriores enviaban también la región.
    # El servidor la deriva siempre del país y sólo acepta una coincidencia.
    region: Optional[Literal['Americas', 'Europe', 'Asia', 'Africa', 'Oceania']] = None


class RankingProfileResponse(BaseModel):
    display_name: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    ranked_profile_ready: bool
    ranking_origin_locked: bool = False
    season_id: str

class LastPlayed(BaseModel):
    stage_id: str
    played_at: datetime

class CareerMeStatsResponse(BaseModel):
    highest_stage_reached: int
    stages_total: int
    max_score: int
    last_played: Optional[LastPlayed] = None
    leaderboard_rank: Optional[int] = None


class CareerRunHistoryEntry(BaseModel):
    stage_run_id: int
    attempt_id: Optional[str] = None
    stage_id: str
    route_position: Optional[int] = None
    difficulty: Literal['easy', 'normal', 'hard']
    correct_answers: int
    flags_total: int
    score: int
    mistakes: int
    hints_used: int
    time_seconds: int
    passed: bool
    played_at: datetime


class CareerRunHistoryResponse(BaseModel):
    items: list[CareerRunHistoryEntry]
    total: int
    season_id: str
