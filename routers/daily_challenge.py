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

from config import settings
from fastapi import Request
from utils.limiter import limiter


@router.get("/today", response_model=daily_challenge_schema.DailyChallengeStatus)
def get_daily_challenge(
    db: Annotated[Session, Depends(get_db)],
    user: Optional[models.User] = Depends(get_current_user_optional),
    x_anonymous_id: Optional[str] = Header(None, alias="X-Anonymous-Id")
):
    today = date.today()
    challenge = daily_challenge_repo.ensure_today_challenge(db, today)
    
    user_id = user.id if user else None
    
    # Validation
    if x_anonymous_id:
        if len(x_anonymous_id) > 64 or not re.match(r"^[a-zA-Z0-9-]+$", x_anonymous_id):
            raise HTTPException(status_code=400, detail="Invalid anonymous ID format")

    if not user_id and not x_anonymous_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide Authorization token or X-Anonymous-Id header"
        )
        
    # Per requirement: "Si hay user válido -> usar user.id. Si no hay user válido -> usar X-Anonymous-Id"
    # We pass BOTH to repo, and repo logic (query filters) will handle prioritization.
    attempt = daily_challenge_repo.get_or_create_attempt(db, challenge, user_id, x_anonymous_id)
    
    max_attempts = settings.DAILY_MAX_ATTEMPTS
    status_str = "solved" if attempt.solved else ("failed" if attempt.failed else "in_progress")
    
    hints_unlocked = daily_challenge_repo.build_hints(challenge, attempt.attempts_used, max_attempts)
    share_text, share_url = daily_challenge_repo.build_share_payload(attempt, challenge, max_attempts, settings.BASE_URL)
    
    correct_answer = None
    if attempt.solved or attempt.failed:
        correct_answer = daily_challenge_schema.GuessAnswer(
            name=challenge.country_name,
            code=challenge.country_code
        )

    return {
        "date": challenge.date,
        "max_attempts": max_attempts,
        "attempts_used": attempt.attempts_used,
        "status": status_str,
        "reveal_level": max_attempts if (attempt.solved or attempt.failed) else min(attempt.attempts_used, max_attempts),
        "can_play": not (attempt.solved or attempt.failed),
        "hints_unlocked": hints_unlocked,
        "hints_total": 3 if max_attempts >= 4 else 2,
        "share_text": share_text,
        "share_url": share_url,
        "correct_answer": correct_answer
    }


@router.get("/today/flag")
@limiter.limit("60/minute")
def get_daily_flag(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user: Optional[models.User] = Depends(get_current_user_optional),
    x_anonymous_id: Optional[str] = Header(None, alias="X-Anonymous-Id")
):
    # Validation
    user_id = user.id if user else None
    if x_anonymous_id:
        if len(x_anonymous_id) > 64 or not re.match(r"^[a-zA-Z0-9-]+$", x_anonymous_id):
            raise HTTPException(status_code=400, detail="Invalid anonymous ID format")

    if not user_id and not x_anonymous_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide Authorization token or X-Anonymous-Id header"
        )

    today = date.today()
    challenge = daily_challenge_repo.ensure_today_challenge(db, today)
    attempt = daily_challenge_repo.get_or_create_attempt(db, challenge, user_id, x_anonymous_id)

    max_attempts = settings.DAILY_MAX_ATTEMPTS
    effective_level = max_attempts if (attempt.solved or attempt.failed) else min(attempt.attempts_used, max_attempts)
    
    from utils.image_processing import pixelate_image
    processed_image_bytes = pixelate_image(
    challenge.flag_image_bytes,
    effective_level,
    seed_date=challenge.date,
    max_level=max_attempts,
)
    
    return Response(content=processed_image_bytes, media_type="image/png")


@router.post("/today/guess", response_model=daily_challenge_schema.GuessResponse)
@limiter.limit("10/minute")
def guess_daily_challenge(
    request: Request,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Must provide Authorization token or X-Anonymous-Id header"
        )

    attempt = daily_challenge_repo.get_or_create_attempt(db, challenge, user_id, x_anonymous_id)
    
    # Optional: Cooling check "last_guess_at" - skipped for now as not in critical path 
    # unless requested (it was "Optional" in requirements)

    return daily_challenge_repo.submit_guess(db, attempt, guess.guess)
