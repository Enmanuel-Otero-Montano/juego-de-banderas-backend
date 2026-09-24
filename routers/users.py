from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from db.models import DailyAttempt, User
from dependencies import get_current_active_user, get_db
from schemas import user_schema

user_router = APIRouter(prefix="/users", tags=["users"])


@user_router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Annotated[Session, Depends(get_db)],
):
    """Elimina la cuenta y los resultados asociados al usuario autenticado."""
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Los intentos diarios se conservan como métricas anónimas, sin vínculo con
    # la persona. Carrera y rankings se eliminan mediante cascade.
    db.query(DailyAttempt).filter(DailyAttempt.user_id == user.id).update(
        {DailyAttempt.user_id: None},
        synchronize_session=False,
    )
    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@user_router.get("/{user_id}/profile-image", include_in_schema=False)
def get_legacy_profile_image(user_id: int):
    """Compatibilidad temporal con clientes que aún usan la ruta anterior."""
    return RedirectResponse(url=f"/user/{user_id}/profile_image", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
