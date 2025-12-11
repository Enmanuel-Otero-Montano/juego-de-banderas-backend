from pydantic import BaseModel
from enum import Enum
from datetime import datetime
from typing import Optional

class ScoreScope(str, Enum):
    global_scope = "global"
    user = "user"
    country = "country"
    region = "region"

class ScoreRequest(BaseModel):
    score: int

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