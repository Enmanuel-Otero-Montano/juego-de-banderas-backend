import re
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.orm import Session

from db import database, models
from dependencies import get_db, get_current_user_optional
from repository import daily_challenge_repo
from schemas import daily_challenge_schema

router = APIRouter(
    prefix="/daily-challenge",
    tags=["Daily Challenge"]
)


@router.get("/today", response_model=daily_challenge_schema.DailyChallengeStatus)
def get_daily_challenge(
    db: Annotated[Session, Depends(get_db)],
    user: Optional[models.User] = Depends(get_current_user_optional),
    x_anonymous_id: Optional[str] = Header(None, alias="X-Anonymous-Id")
):
    today = date.today()
    challenge = daily_challenge_repo.ensure_today_challenge(db, today)
    
    user_id = user.id if user else None
    
    if x_anonymous_id:
        if len(x_anonymous_id) > 64 or not re.match(r"^[a-zA-Z0-9-]+$", x_anonymous_id):
            raise HTTPException(status_code=400, detail="Invalid anonymous ID format")

    if not user_id and not x_anonymous_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide Authorization token or X-Anonymous-Id header"
        )
        
    attempt = daily_challenge_repo.get_or_create_attempt(db, challenge, user_id, x_anonymous_id)
    
    correct_answer = None
    if attempt.solved or attempt.failed:
        correct_answer = {
            "name": challenge.country_name,
            "code": challenge.country_code
        }

    return {
        "date": challenge.date,
        "max_attempts": 6,
        "attempts_used": attempt.attempts_used,
        "status": "solved" if attempt.solved else ("failed" if attempt.failed else "in_progress"),
        "reveal_level": attempt.attempts_used,
        "can_play": not (attempt.solved or attempt.failed),
        "correct_answer": correct_answer
    }


@router.get("/today/flag")
def get_daily_flag(
    db: Annotated[Session, Depends(get_db)],
    reveal_level: int = 0
):
    today = date.today()
    # Ensure challenge exists (crucial if /today/flag is hit before /today)
    challenge = daily_challenge_repo.ensure_today_challenge(db, today)
    
    # Import the pixelation function
    from utils.image_processing import pixelate_image
    
    # Apply pixelation based on reveal level
    processed_image_bytes = pixelate_image(challenge.flag_image_bytes, reveal_level)
    
    return Response(content=processed_image_bytes, media_type="image/png")


@router.post("/today/guess", response_model=daily_challenge_schema.GuessResponse)
def guess_daily_challenge(
    guess: daily_challenge_schema.GuessRequest,
    db: Annotated[Session, Depends(get_db)],
    user: Optional[models.User] = Depends(get_current_user_optional),
    x_anonymous_id: Optional[str] = Header(None, alias="X-Anonymous-Id")
):
    today = date.today()
    challenge = daily_challenge_repo.ensure_today_challenge(db, today)
    
    user_id = user.id if user else None
    
    if x_anonymous_id:
        if len(x_anonymous_id) > 64 or not re.match(r"^[a-zA-Z0-9-]+$", x_anonymous_id):
            raise HTTPException(status_code=400, detail="Invalid anonymous ID format")

    if not user_id and not x_anonymous_id:
        raise HTTPException(status_code=400, detail="Missing auth or anon id")

    attempt = daily_challenge_repo.get_or_create_attempt(db, challenge, user_id, x_anonymous_id)
    
    return daily_challenge_repo.submit_guess(db, attempt, guess.guess)
