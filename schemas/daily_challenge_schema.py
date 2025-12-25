from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


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
    guess: str


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
