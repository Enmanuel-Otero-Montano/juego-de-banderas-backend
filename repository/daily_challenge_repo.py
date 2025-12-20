import hashlib
from datetime import date, datetime
from typing import Optional

import requests
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import models
from schemas import daily_challenge_schema


REST_COUNTRIES_URL = "https://restcountries.com/v3.1/all?fields=name,flags,cca2,cca3"


def get_deterministic_country(date_obj: date):
    """
    Selects a country deterministically based on the date.
    Fetches from REST Countries, filters, sorts, and picks one.
    Returns the country dict (name, flags, cca2, cca3).
    """
    try:
        response = requests.get(REST_COUNTRIES_URL)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"Error fetching countries: {e}")
        raise ValueError("Could not fetch countries")

    # Filter invalid countries
    valid_countries = [
        c for c in data 
        if c.get("flags", {}).get("png") and c.get("cca3") and c.get("name", {}).get("common")
    ]

    if not valid_countries:
        raise ValueError("No valid countries found")

    # Sort stably by cca3
    valid_countries.sort(key=lambda x: x["cca3"])

    # Deterministic selection using date hash
    date_str = date_obj.isoformat()
    # Create a hash of the date string
    hash_obj = hashlib.sha256(date_str.encode("utf-8"))
    # Convert hex digest to integer
    seed_int = int(hash_obj.hexdigest(), 16)
    
    index = seed_int % len(valid_countries)
    return valid_countries[index]


def ensure_today_challenge(db: Session, today: date) -> models.DailyChallenge:
    """
    Ensures a challenge exists for the given date.
    If not, creates it by selecting a country and downloading the flag.
    """
    existing = db.query(models.DailyChallenge).filter(models.DailyChallenge.date == today).first()
    if existing:
        return existing

    # Create new challenge
    country_data = get_deterministic_country(today)
    flag_url = country_data["flags"]["png"]
    
    try:
        flag_response = requests.get(flag_url)
        flag_response.raise_for_status()
        flag_bytes = flag_response.content
    except requests.RequestException as e:
        print(f"Error downloading flag: {e}")
        # In production, we might want a fallback or retry, but for now we fail hard as requested
        raise ValueError(f"Could not download flag from {flag_url}")

    new_challenge = models.DailyChallenge(
        date=today,
        country_name=country_data["name"]["common"],
        country_code=country_data["cca3"],
        flag_image_bytes=flag_bytes,
        created_at=datetime.utcnow()
    )
    db.add(new_challenge)
    db.commit()
    db.refresh(new_challenge)
    return new_challenge


def get_or_create_attempt(
    db: Session, 
    challenge: models.DailyChallenge, 
    user_id: Optional[int], 
    anonymous_id: Optional[str]
) -> models.DailyAttempt:
    """
    Retrieves or creates an attempt for the user/anon on the given challenge.
    """
    query = db.query(models.DailyAttempt).filter(models.DailyAttempt.challenge_id == challenge.id)
    
    if user_id:
        query = query.filter(models.DailyAttempt.user_id == user_id)
    elif anonymous_id:
        query = query.filter(models.DailyAttempt.anonymous_id == anonymous_id)
    else:
        raise ValueError("Must provide user_id or anonymous_id")
        
    attempt = query.first()
    if attempt:
        return attempt

    # Create new attempt
    new_attempt = models.DailyAttempt(
        challenge_id=challenge.id,
        user_id=user_id,
        anonymous_id=anonymous_id,
        attempts_used=0,
        solved=False,
        failed=False,
        created_at=datetime.utcnow()
    )
    db.add(new_attempt)
    db.commit()
    db.refresh(new_attempt)
    return new_attempt


def submit_guess(db: Session, attempt: models.DailyAttempt, guess_text: str) -> daily_challenge_schema.GuessResponse:
    """
    Processes a guess. detailed logic in implementation plan.
    """
    challenge = attempt.challenge
    max_attempts = 6
    
    normalized_guess = guess_text.strip().lower()
    target_name = challenge.country_name.lower()

    # Pre-calculated state
    already_done = attempt.solved or attempt.failed
    
    if already_done:
         # Just return current state
        return _build_response(attempt, challenge, max_attempts, message="Already finished")

    if attempt.attempts_used >= max_attempts:
        # Should have been marked failed, but ensure sync
        if not attempt.failed:
            attempt.failed = True
            db.commit()
        return _build_response(attempt, challenge, max_attempts, message="No attempts left")

    # Process new guess
    is_correct = (normalized_guess == target_name)
    
    # Record guess in DB
    new_guess = models.DailyGuess(
        attempt_id=attempt.id,
        guess_text=guess_text, # store original text
        is_correct=is_correct,
        attempt_number=attempt.attempts_used + 1,
        created_at=datetime.utcnow()
    )
    db.add(new_guess)

    # Update attempt
    attempt.attempts_used += 1
    
    if is_correct:
        attempt.solved = True
        attempt.solved_at = datetime.utcnow()
    elif attempt.attempts_used >= max_attempts:
        attempt.failed = True
    
    db.commit()
    db.refresh(attempt)
    
    return _build_response(attempt, challenge, max_attempts, is_just_solved=is_correct, is_just_failed=attempt.failed)


def _build_response(
    attempt: models.DailyAttempt, 
    challenge: models.DailyChallenge, 
    max_attempts: int,
    message: str = None,
    is_just_solved: bool = False,
    is_just_failed: bool = False
) -> daily_challenge_schema.GuessResponse:
    
    status_str = "in_progress"
    if attempt.solved:
        status_str = "solved"
    elif attempt.failed:
        status_str = "failed"
        
    # Reveal level logic: 0 to 6. simpler mapping
    reveal_level = attempt.attempts_used 
    
    # Construct Answer and Share Text if finished
    answer = None
    share_text = None
    
    if attempt.solved or attempt.failed:
        answer = daily_challenge_schema.GuessAnswer(
            name=challenge.country_name,
            code=challenge.country_code
        )
        
        # Share text generation
        # "Daily Flag YYYY-MM-DD - X/6"
        score_display = str(attempt.attempts_used) if attempt.solved else "X"
        date_str = challenge.date.isoformat()
        
        emoji_line = ""
        # We need to recreate the history of correctness.
        # Since we might not have the list of daily_guesses passed here, 
        # we can infer valid logic or fetch guesses if needed.
        # But for simpler implementation, let's just use squares.
        # Ideally we fetch the guesses to generate the exact emoji sequence.
        # But since we just saved the guess, or it's an existing attempt, 
        # let's assume valid guesses are stored.
        # For this turn, I will just generate a simple summary string if I can't access guesses easily.
        # Actually `attempt.guesses` should be available via relationship if eager loaded or lazily fetched.
        
        emojis = []
        # Sort guesses by number
        sorted_guesses = sorted(attempt.guesses, key=lambda g: g.attempt_number)
        for g in sorted_guesses:
            if g.is_correct:
                emojis.append("🟩")
            else:
                emojis.append("🟥")
        
        emoji_line = "".join(emojis)

        share_text = f"Daily Flag {date_str} - {score_display}/{max_attempts}\n{emoji_line}"

    return daily_challenge_schema.GuessResponse(
        status=status_str,
        attempts_used=attempt.attempts_used,
        max_attempts=max_attempts,
        reveal_level=reveal_level,
        attempts_left=max_attempts - attempt.attempts_used,
        message=message,
        answer=answer,
        share_text=share_text
    )
