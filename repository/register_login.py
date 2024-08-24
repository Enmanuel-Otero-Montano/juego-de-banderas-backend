from sqlalchemy.orm import Session

from db import models


def check_user_exist(db: Session, user_email: str):
    return db.query(models.User).filter(models.User.email == user_email).first()


def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()
