from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import date
from db.models import User, OverallScoreTable


def save_score(db: Session, score: int, current_user: User):
    # Intentá leer la fila del usuario; con_for_update si tu DB es Postgres
    query = db.query(OverallScoreTable).filter(OverallScoreTable.user_id == current_user.id)
    try:
        user_score = query.with_for_update(read=True).first()
    except Exception:
        # Si no es Postgres o falla el lock, cae en lectura normal
        user_score = query.first()

    if user_score:
        if user_score.max_score is None or score > user_score.max_score:
            user_score.max_score = score
            user_score.date_max_score = date.today()
        user_score.last_score = score
        user_score.date_last_score = date.today()
        db.add(user_score)
    else:
        user_score = OverallScoreTable(
            user_id=current_user.id,
            max_score=score,
            last_score=score,
            date_max_score=date.today(),
            date_last_score=date.today(),
        )
        db.add(user_score)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        # En caso extremo de carrera, vuelve a actualizar (ya debe existir)
        existing = db.query(OverallScoreTable).filter(OverallScoreTable.user_id == current_user.id).first()
        if existing:
            if existing.max_score is None or score > existing.max_score:
                existing.max_score = score
                existing.date_max_score = date.today()
            existing.last_score = score
            existing.date_last_score = date.today()
            db.add(existing)
            db.commit()
            db.refresh(existing)
            return existing
        else:
            # Reintento de inserción única
            db.add(user_score)
            db.commit()

    db.refresh(user_score)
    return user_score


def get_overall_score_table(db: Session):
    # Ranking: mejor primero; empate por fecha de récord más antigua (quien lo logró antes)
    return (
        db.query(OverallScoreTable)
        .order_by(OverallScoreTable.max_score.desc(), OverallScoreTable.date_max_score.asc())
        .all()
    )

def get_public_ranking(db, limit: int | None = None, offset: int | None = None):
    q = (
        db.query(
            User.id,
            User.username,
            User.profile_image,
            OverallScoreTable.max_score,
            OverallScoreTable.date_max_score,
        )
        .join(User, User.id == OverallScoreTable.user_id)
        .order_by(
            OverallScoreTable.max_score.desc(),
            OverallScoreTable.date_max_score.asc(),
            User.username.asc(),
        )
    )

    if offset:
        q = q.offset(offset)
    if limit:
        q = q.limit(limit)

    rows = q.all()
    return [
        {
            "user_id": r[0],
            "username": r[1],
            "has_profile_image": bool(r[2]),
            "max_score": r[3],
            "date_max_score": r[4],
        }
        for r in rows
    ]



