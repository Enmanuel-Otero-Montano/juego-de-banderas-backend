from sqlalchemy.orm import Session, joinedload

from db.models import User, OverallScoreTable


def save_score(db: Session, score: int, current_user: User):
    user_score = db.query(OverallScoreTable).filter(OverallScoreTable.user_id == current_user.id).first()

    if user_score:
        # Si ya existe un score, actualizar max_score si es menor al nuevo score
        if score > user_score.max_score:
            user_score.max_score = score
            user_score.date_max_score = date.today()

        # Actualizar siempre el último score y la fecha del último score
        user_score.last_score = score
        user_score.date_last_score = date.today()

        db.add(user_score)
        db.commit()
        db.refresh(user_score)
    else:
        # Si no existe un score, crear uno nuevo para el usuario
        new_score = OverallScoreTable(
            user_id=current_user.id,
            max_score=score,
            last_score=score,
            date_max_score=date.today(),
            date_last_score=date.today()
        )
        db.add(new_score)
        db.commit()
        db.refresh(new_score)
        user_score = new_score

    return user_score


def get_overall_score_table(db: Session):
    scores = db.query(OverallScoreTable).all()
    return scores
