from sqlalchemy.orm import Session

from db import models
from schemas import user_schema


def check_user_exist(db: Session, user_email: str):
    return db.query(models.User).filter(models.User.email == user_email).first()


def get_user_by_username(db: Session, username: str):
    return db.query(models.User).filter(models.User.username == username).first()

def update_user_profile(db: Session, user_id: int, user_profile_update: user_schema.UserProfileUpdate, delete_current_profile_image: bool):
    db_user = db.query(models.User).filter(models.User.id == user_id).first()
    if db_user:
        # Comprobar si el nuevo nombre de usuario ya existe
        existing_user = db.query(models.User).filter(models.User.username == user_profile_update.username, models.User.id != user_id).first()
        if existing_user:
            raise ValueError("El nombre de usuario ya está en uso")
        
        if user_profile_update.profile_image:
            update_profile_image = user_profile_update.profile_image
        elif not user_profile_update.profile_image and delete_current_profile_image:
            update_profile_image = None
        else:
            update_profile_image = db_user.profile_image
        
        db_user.username = user_profile_update.username
        db_user.full_name = user_profile_update.full_name
        db_user.country = user_profile_update.country
        db_user.profile_image = update_profile_image
        db.commit()
        db.refresh(db_user)
        return db_user
    else:
        raise ValueError("Usuario no encontrado")
    
def get_user_profile(db: Session, user_id: int):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    return user
