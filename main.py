from typing import Annotated
from datetime import timedelta, datetime, timezone

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBasic, OAuth2PasswordRequestForm, OAuth2PasswordBearer
from sqlalchemy.orm import Session
from passlib.context import CryptContext
from dotenv import load_dotenv

import smtplib
from email.mime.text import MIMEText

from repository import register_login, scores_repo
from schemas import user_schema, token
from db import database, models
import jwt
import os

from jwt.exceptions import InvalidTokenError

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

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/register", response_model=user_schema.User)
def register_user(user: user_schema.UserCreate, db: Session = Depends(get_db)):
    db_user = register_login.check_user_exist(db, user.email)
    if db_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    hashed_password = get_password_hash(user.password)
    new_user = models.User(
        username=user.username,
        email=user.email,
        full_name=user.full_name,
        hashed_password=hashed_password
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    verification_token = create_email_verification_token(user.email)
    send_verification_email(user.email, verification_token)
    return new_user


def create_email_verification_token(email: str):
    expire = datetime.utcnow() + timedelta(hours=1)  # Token válido por 1 hora
    to_encode = {"sub": email, "exp": expire}
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


def send_verification_email(email: str, token: str):
    verification_link = f"{VERIFICATION_LINK}{token}"
    subject = "Verificación de correo electrónico"
    body = f"Haz clic en el siguiente enlace para verificar tu correo: {verification_link}"

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
    return {"msg": "Email verified successfully"}


@app.post("/resend-verification-email")
def resend_verification_email(email: str, db: Session = Depends(get_db)):
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
    if not user or not user.is_verified:
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
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = token.TokenData(username=username)
    except InvalidTokenError:
        raise credentials_exception
    user = get_user(db, username=token_data.username)
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: Annotated[user_schema.User, Depends(get_current_user)], ):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


@app.post("/token")
async def login_for_access_token(form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
                                 db: Session = Depends(get_db)) -> token.Token:
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password",
                            headers={"WWW-Authenticate": "Bearer"}, )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.username}, expires_delta=access_token_expires)
    return token.Token(access_token=access_token, token_type="bearer")


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
    print(all_scores,"all_scores")
    return all_scores
