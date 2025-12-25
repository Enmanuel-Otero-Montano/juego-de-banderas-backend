import hashlib
from datetime import date, datetime
from typing import Optional

import requests
from sqlalchemy.orm import Session
from sqlalchemy import func

from db import models
from schemas import daily_challenge_schema


from config import settings

REST_COUNTRIES_URL = "https://restcountries.com/v3.1/all?fields=name,flags,cca2,cca3,region,subregion,capital,latlng,population,languages"


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

    # Prepare languages string
    langs = country_data.get("languages", {})
    languages_str = ",".join(langs.values()) if langs else None
    
    capital_list = country_data.get("capital", [])
    capital_val = capital_list[0] if capital_list else None
    
    latlng = country_data.get("latlng", [])
    lat_val = latlng[0] if len(latlng) > 0 else None
    lng_val = latlng[1] if len(latlng) > 1 else None

    new_challenge = models.DailyChallenge(
        date=today,
        country_name=country_data["name"]["common"],
        country_code=country_data["cca3"],
        flag_image_bytes=flag_bytes,
        region=country_data.get("region"),
        subregion=country_data.get("subregion"),
        capital=capital_val,
        latitude=lat_val,
        longitude=lng_val,
        population=country_data.get("population"),
        languages=languages_str,
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


def build_hints(challenge: models.DailyChallenge, attempts_used: int, max_attempts: int):
    """
    Returns a list of unlocked hints based on attempts used and max attempts.
    """
    hints_unlocked = []
    
    # attempts_used >= 0: Pista 1 "Continente/Región"
    if attempts_used >= 0:
        val = challenge.region or "Desconocido"
        hints_unlocked.append({"title": "Continente/Región", "value": val})
        
    # attempts_used >= 1: Pista 2 "Hemisferio"
    if attempts_used >= 1:
        lat = challenge.latitude
        lng = challenge.longitude
        if lat is not None and lng is not None:
            ns = "Norte" if lat >= 0 else "Sur"
            ew = "Este" if lng >= 0 else "Oeste"
            val = f"{ns} y {ew}"
        else:
            val = "Desconocido"
        hints_unlocked.append({"title": "Hemisferio", "value": val})
            
    # attempts_used >= 2: Pista 3 "Subregión" (solo si max_attempts >= 4)
    if attempts_used >= 2 and max_attempts >= 4:
        val = challenge.subregion or "Desconocida"
        hints_unlocked.append({"title": "Subregión", "value": val})
             
    return hints_unlocked


def build_share_payload(attempt: models.DailyAttempt, challenge: models.DailyChallenge, max_attempts: int, base_url: str):
    """
    Returns (share_text, share_url) if finished, else (None, None).
    """
    if not (attempt.solved or attempt.failed):
        return None, None

    score_display = str(attempt.attempts_used) if attempt.solved else "X"
    date_str = challenge.date.isoformat()
    
    emojis = []
    if attempt.guesses:
        sorted_guesses = sorted(attempt.guesses, key=lambda g: g.attempt_number)
        for g in sorted_guesses:
            if g.is_correct:
                emojis.append("🟩")
            else:
                emojis.append("🟥")
    
    emoji_line = "".join(emojis)
    share_url = f"{base_url}/daily-challenge.html?date={date_str}&utm_source=share"
    share_text = f"Bandera Diaria {date_str}\n{score_display}/{max_attempts}\n{emoji_line}"
    
    return share_text, share_url


def submit_guess(db: Session, attempt: models.DailyAttempt, guess_text: str) -> daily_challenge_schema.GuessResponse:
    """
    Processes a guess. detailed logic in implementation plan.
    """
    challenge = attempt.challenge
    max_attempts = settings.DAILY_MAX_ATTEMPTS
    
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
        
    hints_unlocked = build_hints(challenge, attempt.attempts_used, max_attempts)
    reveal_level = max_attempts if (attempt.solved or attempt.failed) else min(attempt.attempts_used, max_attempts)
    
    share_text, share_url = build_share_payload(attempt, challenge, max_attempts, settings.BASE_URL)
    
    correct_answer = None
    if attempt.solved or attempt.failed:
        correct_answer = daily_challenge_schema.GuessAnswer(
            name=challenge.country_name,
            code=challenge.country_code
        )

    return daily_challenge_schema.GuessResponse(
        status=status_str,
        attempts_used=attempt.attempts_used,
        max_attempts=max_attempts,
        reveal_level=reveal_level,
        attempts_left=max(0, max_attempts - attempt.attempts_used),
        is_correct=is_just_solved,
        hints_unlocked=hints_unlocked,
        share_text=share_text,
        share_url=share_url,
        correct_answer=correct_answer
    )
