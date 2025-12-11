from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from db.models import User
from dependencies import get_db

user_router = APIRouter(prefix="/users", tags=["users"])

@user_router.get("/{user_id}/profile-image")
def get_profile_image(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.profile_image:
        raise HTTPException(status_code=404, detail="Profile image not found")

    # Ajustá el media_type si tus imágenes no son PNG
    return Response(content=user.profile_image, media_type="image/png")
