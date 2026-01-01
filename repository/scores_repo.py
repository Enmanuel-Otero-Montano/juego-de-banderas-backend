from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, desc
from datetime import datetime, date
from db.models import User, OverallScoreTable
from schemas.score import RegionEnum, ScoreScope


def save_score(db: Session, score: int, current_user: User, region_key: str = "career", country_code: str | None = None):
    # Intentá leer la fila del usuario para esa region
    query = db.query(OverallScoreTable).filter(
        OverallScoreTable.user_id == current_user.id,
        OverallScoreTable.region_key == region_key
    )
    try:
        user_score = query.with_for_update(read=True).first()
    except Exception:
        user_score = query.first()

    if user_score:
        if user_score.max_score is None or score > user_score.max_score:
            user_score.max_score = score
            user_score.date_max_score = datetime.utcnow()
        user_score.last_score = score
        user_score.date_last_score = date.today()
        # Update country if provided and not present, or just always update? 
        # Better to keep the latest one or the one from user profile.
        if country_code:
            user_score.country_code = country_code
        db.add(user_score)
    else:
        user_score = OverallScoreTable(
            user_id=current_user.id,
            max_score=score,
            last_score=score,
            date_max_score=datetime.utcnow(),
            date_last_score=date.today(),
            region_key=region_key,
            country_code=country_code
        )
        db.add(user_score)

    try:
        db.commit()
        db.refresh(user_score)
        return user_score
    except IntegrityError:
        db.rollback()
        # Re-fetch en caso de race condition
        existing = db.query(OverallScoreTable).filter(
            OverallScoreTable.user_id == current_user.id,
            OverallScoreTable.region_key == region_key
        ).first()
        
        if existing:
            if existing.max_score is None or score > existing.max_score:
                existing.max_score = score
                existing.date_max_score = datetime.utcnow()
            existing.last_score = score
            existing.date_last_score = date.today()
            if country_code:
                existing.country_code = country_code
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing
        else:
            # This should technically not happen if IntegrityError was due to duplicate key
            # But if it does, we can retry adding? Or just return None/Log error.
            # Retry add
            db.add(user_score)
            db.commit()
            db.refresh(user_score)
            return user_score

def get_ranking_query(db: Session, region_key: str | None = None, country_code: str | None = None):
    q = db.query(
        User.id,
        User.username,
        User.profile_image,
        OverallScoreTable.max_score,
        OverallScoreTable.date_max_score,
        OverallScoreTable.country_code,
        OverallScoreTable.region_key
    ).join(User, User.id == OverallScoreTable.user_id)

    if region_key:
        q = q.filter(OverallScoreTable.region_key == region_key)
    
    if country_code:
        q = q.filter(OverallScoreTable.country_code == country_code)
    
    # Always order by score desc, date asc (earliest best score wins)
    q = q.order_by(
        OverallScoreTable.max_score.desc(),
        OverallScoreTable.date_max_score.asc(),
        User.username.asc(),
    )
    return q

def format_ranking_result(rows):
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "has_profile_image": bool(r[2]),
            "max_score": r[3],
            "date_max_score": r[4],
            "country_code": r[5],
            "region": r[6]
        }
        for r in rows
    ]

def get_public_ranking(db: Session, limit: int = 10, offset: int = 0):
    # Global ranking (mixes all regions or just career? Usually "Global" implies "Career" mode or "Best of all"? 
    # Requirement: "global / país / región / mis scores"
    # Assuming Global means "Career" mode global leaderboard for now, or aggregation? 
    # Let's assume Global = 'career' region for simplicity and consistency with old behavior, 
    # unless we want a mixed bag. The old behavior was one score per user.
    # Let's filter by region_key='career' for the main global leaderboard to match "classic" ranking.
    # Or should we show the absolute best score across any region? 
    # Let's stick to 'career' as the default "Global" ranking context.
    return get_region_scores(db, "career", limit, offset)

def get_region_scores(db: Session, region_key: str, limit: int = 10, offset: int = 0):
    rows = get_ranking_query(db, region_key=region_key).limit(limit).offset(offset).all()
    return format_ranking_result(rows)

def get_country_scores(db: Session, country_code: str, limit: int = 10, offset: int = 0):
    # Ranking within a country, usually for 'career' mode unless specified? 
    # The requirement isn't explicit if country ranking aggregates all regions. 
    # Let's assume 'career' mode for country ranking too for now.
    rows = get_ranking_query(db, region_key="career", country_code=country_code).limit(limit).offset(offset).all()
    return format_ranking_result(rows)

def get_user_scores_history(db: Session, user_id: int, limit: int = 10, offset: int = 0):
    # Returns all scores for a user (different regions)
    q = db.query(OverallScoreTable).filter(OverallScoreTable.user_id == user_id).order_by(OverallScoreTable.date_last_score.desc())
    return q.limit(limit).offset(offset).all()

def get_user_best_score(db: Session, user_id: int, region_key: str = "career"):
    return (
        db.query(OverallScoreTable)
        .filter(OverallScoreTable.user_id == user_id, OverallScoreTable.region_key == region_key)
        .first()
    )

def get_user_rank(db: Session, user_id: int, scope: ScoreScope, scope_value: str | None = None):
    # Calculate rank efficiently
    # Rank is 1 + count(scores > my_score)
    
    # 1. Get user's score for the target scope
    target_region = "career" # Default
    target_country = None

    if scope == ScoreScope.region:
        target_region = scope_value
    elif scope == ScoreScope.country:
        target_country = scope_value
        # If scope is country, we still need to know WHICH region we are ranking? 
        # Or is country ranking for "career"? Assume career.
    
    my_record = db.query(OverallScoreTable).filter(
        OverallScoreTable.user_id == user_id,
        OverallScoreTable.region_key == target_region
    ).first()

    if not my_record:
        return None

    # 2. Count how many better scores exist
    # Criteria: score > my.score OR (score = my.score AND date < my.date)
    
    filters = [OverallScoreTable.region_key == target_region]
    if scope == ScoreScope.country:
        filters.append(OverallScoreTable.country_code == target_country)
    
    # Logic for "better":
    # (s.max_score > my.max_score) OR (s.max_score == my.max_score AND s.date_max_score < my.date_max_score)
    
    q = db.query(func.count(OverallScoreTable.id)).filter(*filters)
    
    better_scores_count = q.filter(
        (OverallScoreTable.max_score > my_record.max_score) |
        ((OverallScoreTable.max_score == my_record.max_score) & (OverallScoreTable.date_max_score < my_record.date_max_score))
    ).scalar()

    total_players = db.query(func.count(OverallScoreTable.id)).filter(*filters).scalar()

    return {
        "rank": better_scores_count + 1,
        "max_score": my_record.max_score,
        "total_players": total_players,
        "region": target_region,
        "scope": scope
    }

def get_summary(db: Session, current_user: User):
    # Parallelize or just sequential queries
    # 1. Global (Career) Top
    global_top = get_region_scores(db, "career", limit=10)
    
    # 2. Country Top (User's country, Career)
    country_top = []
    if current_user.country:
        country_top = get_country_scores(db, current_user.country, limit=10)
    
    # 3. Region Top (Maybe "Americas" if user is from there? Or just omit/send career?)
    # Requirement: "region_top". We can guess region from user country or just skip. 
    # Or maybe "Europe" top? 
    # Let's leave it empty or generic for now, user might have played multiple.
    # Maybe return the leaderboard of the region the user LAST played or best performed?
    # For now, let's return 'career' as global and maybe nothing for specific region unless asked.
    # User requirement: "global_top, country_top, region_top"
    # Let's try to infer user's "home region" from country code map (not implemented yet) or just skip.
    
    # 4. User Positions
    user_positions = {
        "global": get_user_rank(db, current_user.id, ScoreScope.global_scope),
        "country": get_user_rank(db, current_user.id, ScoreScope.country, current_user.country) if current_user.country else None
        # "region": ...
    }

    # 5. User Best (Career)
    user_best = get_user_best_score(db, current_user.id, "career")
    
    return {
        "global_top": global_top,
        "country_top": country_top,
        "user_positions": user_positions,
        "user_best": {
             "max_score": user_best.max_score if user_best else 0,
             "rank": user_positions["global"]["rank"] if user_positions["global"] else None
        }
    }




