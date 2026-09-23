from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class GuessAnswer(BaseModel):
    name: str
    code: str


class Hint(BaseModel):
    title: str
    value: str


class DailyChallengeStatus(BaseModel):
    date: date
    max_attempts: int
    attempts_used: int
    status: str  # "in_progress", "solved", "failed"
    reveal_level: int
    can_play: bool
    hints_unlocked: list[Hint] = []
    hints_total: int = 0
    share_text: Optional[str] = None
    share_url: Optional[str] = None
    correct_answer: Optional[GuessAnswer] = None


class GuessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Se persiste el texto original del intento: mantenerlo acotado evita que
    # una petición autenticada o anónima convierta la tabla en almacenamiento
    # arbitrario.
    guess: str = Field(min_length=1, max_length=120)


class GuessResponse(BaseModel):
    status: str  # "in_progress", "solved", "failed"
    attempts_used: int
    max_attempts: int
    reveal_level: int
    attempts_left: int
    is_correct: bool
    hints_unlocked: list[Hint] = []
    share_text: Optional[str] = None
    share_url: Optional[str] = None
    correct_answer: Optional[GuessAnswer] = None
