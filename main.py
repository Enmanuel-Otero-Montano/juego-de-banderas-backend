from typing import Annotated, Optional
from datetime import timedelta, datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, status, Body, Form, UploadFile, File
from fastapi.security import HTTPBasic, OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware

import smtplib
from email.mime.text import MIMEText

from PIL import Image
from io import BytesIO

from repository import register_login, scores_repo
from schemas import user_schema, token
from db import database, models
import jwt
import os

from jwt.exceptions import InvalidTokenError

from schemas.user_schema import UserRegisterResponse

# from schemas.overall_score_schema import ScoreRequest

database.Base.metadata.create_all(bind=database.engine)

load_dotenv()

app = FastAPI()

security = HTTPBasic()
# Variables de entorno
SECRET_KEY = os.getenv('SECRET_KEY')
ACCESS_TOKEN_EXPIRE_MINUTES = float(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', 2880))
ALGORITHM = os.getenv('ALGORITHM')
SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = os.getenv('SMTP_PORT')
SENDER_EMAIL = os.getenv('SENDER_EMAIL')
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
VERIFICATION_LINK = os.getenv('VERIFICATION_LINK')
BASE_URL = os.getenv('BASE_URL')

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[BASE_URL],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/register", response_model=UserRegisterResponse)
async def register_user(username: Annotated[str, Form()], full_name: Annotated[str, Form()], email: Annotated[str, Form()], password: Annotated[str, Form()], profile_image: Annotated[UploadFile, File()], db: Session = Depends(get_db)):
    # TODO: Tengo que agregar una validación para el nombre de usuario
    db_user = register_login.check_user_exist(db, email)
    image_content = await profile_image.read()

    if db_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
    if len(image_content) > 2 * 1024 * 1024:  # Limite de 2MB como ejemplo
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Image too large")
    
    hashed_password = get_password_hash(password)
    new_user = models.User(
        username=username,
        email=email,
        full_name=full_name,
        hashed_password=hashed_password,
        profile_image=image_content
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    name = full_name if full_name else username

    verification_token = create_email_verification_token(email)
    send_verification_email(email, verification_token, name)
    return new_user


def create_email_verification_token(email: str):
    expire = datetime.now(timezone.utc) + timedelta(hours=1)  # Token válido por 1 hora
    to_encode = {"sub": email, "exp": expire}
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


def send_verification_email(email: str, token: str, name: str):
    verification_link = f"{VERIFICATION_LINK}{token}"
    subject = "¡Bienvenido a Banderas, países y regiones! Verifica tu cuenta para comenzar"
    body = f"""
    Hola {name},
    
    ¡Gracias por registrarte en Banderas, países y regiones! Estamos felices de que te unas a nuestra comunidad.
    
    Para completar tu registro y activar tu cuenta, simplemente haz clic en el siguiente enlace:
    
    {verification_link}
    
    Si no solicitaste esta cuenta, puedes ignorar este correo.
    
    Estamos aquí para ayudarte en cualquier momento. Si tienes alguna pregunta, no dudes en responder a este correo.
    
    ¡Esperamos que disfrutes de nuestra plataforma!
    
    Saludos cordiales,
    El equipo de Banderas, países y regiones
    """

    smtp_server = SMTP_SERVER
    smtp_port = SMTP_PORT
    sender_email = SENDER_EMAIL
    sender_password = SENDER_PASSWORD

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = email

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, email, msg.as_string())
    except Exception as e:
        print(f"Error enviando correo: {e}")


@app.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid token")

    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Marcar el usuario como verificado
    user.is_verified = True
    db.commit()
    return RedirectResponse(f"{BASE_URL}/pages/successful-verification.html")


@app.post("/resend-verification-email")
def resend_verification_email(email: Annotated[str, Body()], db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.is_verified:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already verified")

    # Generar un nuevo token y reenviar el correo
    verification_token = create_email_verification_token(user.email)
    send_verification_email(user.email, verification_token)

    return {"msg": "Verification email resent successfully"}


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def get_user(db, username: str):
    db_user = register_login.get_user_by_username(db, username)
    if db_user:
        return db_user
    return None


def authenticate_user(username: str, password: str, db: Session):
    user = get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(user_token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(user_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        token_data = token.TokenData(user_id=user_id)
    except InvalidTokenError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.id == token_data.user_id).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: Annotated[user_schema.User, Depends(get_current_user)], ):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


@app.post("/login")
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
                                 db: Session = Depends(get_db)):
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"message": "Usuario o contraseña incorrectos"},
                            headers={"WWW-Authenticate": "Bearer"}, )
    if user and not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"message": "Usuario no verificado", "email": user.email},
                            headers={"WWW-Authenticate": "Bearer"})
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.id}, expires_delta=access_token_expires)
    full_name = user.full_name if user.full_name else user.username
    profile_image_url = f"/user/{user.id}/profile_image"
    return {"access_token": access_token, "token_type": "bearer", "full_name": full_name, "profile_image_url": profile_image_url, "user_id": user.id}

@app.get("/user/{user_id}/profile_image")
async def get_profile_image(user_id: int, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or not user.profile_image:
        raise HTTPException(status_code=404, detail="Image not found")
    
    try:
        image = Image.open(BytesIO(user.profile_image))
        image_format = image.format
        if image_format == "JPEG":
            media_type = "image/jpeg"
        elif image_format == "PNG":
            media_type = "image/png"
        else:
            raise HTTPException(status_code=400, detail="Unsupported image format")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid image file")

    return Response(content=user.profile_image, media_type=media_type)


@app.get("/users/me", response_model=user_schema.User)
async def read_users_me(current_user: Annotated[user_schema.User, Depends(get_current_active_user)], ):
    return current_user


@app.post("/save-overrall-score")
async def save_overrall_score(current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
                              score_to_save: user_schema.ScoreRequest, db: Annotated[Session, Depends(get_db)]):
    score = scores_repo.save_score(db, score_to_save.score, current_user)
    return score


@app.get("/overall-scores", response_model=list[user_schema.OverallScore])
async def get_overall_score_table(db: Annotated[Session, Depends(get_db)]):
    all_scores = scores_repo.get_overall_score_table(db)
    return all_scores


@app.put("/user/profile", response_model=user_schema.UserRegisterResponse)
async def update_user_profile(username: Annotated[str, Form()], full_name: Annotated[Optional[str], Form()], profile_image: Annotated[Optional[UploadFile], File()], country: Annotated[str, Form()], current_user: Annotated[user_schema.User, Depends(get_current_active_user)], delete_current_profile_image: Annotated[bool, Form()] = False, db: Session = Depends(get_db)):
    profile_image_bytes = await profile_image.read() if profile_image else None
    user_profile_update = user_schema.UserProfileUpdate(
        username=username,
        full_name=full_name,
        profile_image=profile_image_bytes,
        country=country
    )
    updated_user = register_login.update_user_profile(db, current_user.id, user_profile_update, delete_current_profile_image)
    return updated_user

@app.get("/user-profile/{user_id}", response_model=user_schema.UserEditProfileCurrentData)
async def get_user_profile(user_id: int, current_user: Annotated[user_schema.User, Depends(get_current_active_user)], db: Session = Depends(get_db)):
    user_profile = register_login.get_user_profile(db, user_id)
    if not user_profile:
        raise HTTPException(status_code=401, detail="User not found")
    return user_profile