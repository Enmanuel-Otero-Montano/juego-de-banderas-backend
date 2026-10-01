from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

RaceScope = Literal["World", "Americas", "Europe", "Asia", "Africa", "Oceania"]
RaceDifficulty = Literal["easy", "normal", "hard"]


class RaceRoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: RaceScope = "World"
    difficulty: RaceDifficulty = "normal"


class RaceRoomJoin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = Field(default=None, min_length=6, max_length=6)
    token: str | None = Field(default=None, min_length=32, max_length=256)

    @model_validator(mode="after")
    def require_one_invitation(self):
        if bool(self.code) == bool(self.token):
            raise ValueError("Provide exactly one room code or invite token")
        if self.code:
            self.code = self.code.strip().upper()
        return self


class RaceRoomUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: RaceScope | None = None
    difficulty: RaceDifficulty | None = None


class RaceMemberResponse(BaseModel):
    user_id: int
    display_name: str
    seat: int
    role: Literal["host", "player"]
    ready: bool
    connected: bool
    intermission_state: str


class RaceRoomResponse(BaseModel):
    id: str
    code: str
    invite_url: str
    status: str
    scope: RaceScope
    difficulty: RaceDifficulty
    host_user_id: int
    current_user_id: int
    revision: int
    expires_at: datetime
    members: list[RaceMemberResponse]
    current_round: dict | None = None


class RaceAnswerMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    sequence: int = Field(ge=1, le=100)
    country_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")
    selected_code: str = Field(min_length=2, max_length=2, pattern=r"^[A-Za-z]{2}$")


class RaceIntermissionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["reviewing_result", "ad_break", "in_lobby"]
