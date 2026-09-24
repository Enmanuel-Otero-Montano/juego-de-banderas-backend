from typing import Annotated, Optional
from datetime import timedelta, datetime, timezone

from fastapi import BackgroundTasks, FastAPI, HTTPException, Depends, status, Body, Form, UploadFile, File, Query, Request, Cookie

from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from fastapi.responses import HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError

from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette import status as starlette_status

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from utils.limiter import limiter
from utils.auth_rate_limit import consume_auth_attempt
from utils.client_ip import get_client_ip

from sqlalchemy.orm import Session
from config import settings
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from pwdlib.hashers.bcrypt import BcryptHasher
from pydantic import EmailStr

import smtplib
from email.mime.text import MIMEText
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from urllib.parse import quote
from uuid import uuid4

from PIL import Image
from PIL import UnidentifiedImageError
from io import BytesIO
import warnings

from repository import register_login
from schemas import user_schema, token
from routers import users, daily_challenge, health, career
from db import database, models

import jwt
import os

from jwt.exceptions import InvalidTokenError
from jwt import ExpiredSignatureError, InvalidSignatureError, DecodeError

import logging
from logging.handlers import RotatingFileHandler
import sys

from schemas.user_schema import UserRegisterResponse

# Las migraciones de Alembic son la única autoridad de esquema en producción.
# create_all se conserva en desarrollo/test para facilitar el arranque local.
if settings.ENV != "production":
    database.Base.metadata.create_all(bind=database.engine)

def api_documentation_urls(environment: str) -> dict[str, str | None]:
    """No publicar el inventario de la API en el entorno de producción."""
    if environment == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(**api_documentation_urls(settings.ENV))

# Configurar logging
def setup_logging():
    logger = logging.getLogger("atlas")
    logger.setLevel(logging.INFO if settings.ENV == "production" else logging.DEBUG)
    logger.propagate = False
    if logger.handlers:
        return logger
    
    # Formato
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # En producción se escribe únicamente a stdout: funciona en contenedores
    # con filesystem de solo lectura y deja la persistencia al proveedor.
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if settings.ENV != "production":
        import os
        try:
            os.makedirs("logs", exist_ok=True)
            file_handler = RotatingFileHandler(
                "logs/app.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError:
            logger.warning("No se pudo crear el log local; se mantiene únicamente stdout")
    
    return logger

logger = setup_logging()

# Rate limiting configuration
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.exception_handler(RateLimitExceeded)
async def custom_rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={
            "error": 429,
            "message": "Demasiadas solicitudes. Por favor intenta más tarde.",
            "retry_after": exc.detail,  # Tiempo en segundos para reintentar
            "path": str(request.url.path),
        },
    )

# === CORS estricto según entorno ===
# En dev añadimos orígenes locales comunes
_local_dev = [
    "http://127.0.0.1:5500", "http://localhost:5500",
    "http://127.0.0.1:5173", "http://localhost:5173", "http://localhost:3000",
    "capacitor://localhost", "https://localhost"
]
allow_origins = (
    settings.ALLOWED_ORIGINS
    if settings.ENV == "production"
    else list({*settings.ALLOWED_ORIGINS, *_local_dev})
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=False,  # True solo si vas a usar cookies/sesiones cross-site
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Type"],
    max_age=600,
)

app.include_router(users.user_router)
app.include_router(daily_challenge.router)
app.include_router(health.router)
app.include_router(career.router)  # Career mode endpoints

# === Handlers de error coherentes ===
@app.exception_handler(StarletteHTTPException)
async def http_exc_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.status_code,
            "message": exc.detail or "HTTP error",
            "path": str(request.url.path),
        },
    )

@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=starlette_status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "error": 422,
            "message": "Parámetros inválidos",
            "details": exc.errors(),
            "path": str(request.url.path),
        },
    )

@app.exception_handler(Exception)
async def unhandled_exc_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception on {request.method} {request.url.path}",
        exc_info=True,  # Incluye traceback completo
        extra={
            "client_ip": get_client_ip(request),
            "user_agent": request.headers.get("user-agent", "unknown")
        }
    )
    return JSONResponse(
        status_code=starlette_status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": 500,
            "message": "Ocurrió un error inesperado",
            "path": str(request.url.path),
        },
    )

# Variables de entorno
SECRET_KEY = settings.SECRET_KEY.get_secret_value()
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES
REFRESH_TOKEN_EXPIRE_DAYS = settings.REFRESH_TOKEN_EXPIRE_DAYS
ALGORITHM = settings.ALGORITHM
SMTP_SERVER = settings.SMTP_SERVER
SMTP_PORT = settings.SMTP_PORT
SMTP_TIMEOUT_SECONDS = settings.SMTP_TIMEOUT_SECONDS
SENDER_EMAIL = settings.SENDER_EMAIL
SENDER_PASSWORD = settings.SENDER_PASSWORD.get_secret_value() if settings.SENDER_PASSWORD else None
VERIFICATION_LINK = settings.VERIFICATION_LINK
BASE_URL = settings.BASE_URL


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")
password_hash = PasswordHash((Argon2Hasher(), BcryptHasher()))

MAX_PROFILE_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PROFILE_IMAGE_PIXELS = 16_000_000
PROFILE_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png"}
PROFILE_IMAGE_FORMATS = {"JPEG", "PNG"}


async def read_valid_profile_image(profile_image: UploadFile | None) -> bytes | None:
    """Lee una imagen de perfil acotada y comprueba su formato real.

    El Content-Type del cliente no es suficiente: Pillow verifica la cabecera
    del archivo y el límite de píxeles evita imágenes comprimidas que ocupen
    poca memoria en tránsito pero se expandan excesivamente al procesarlas.
    """
    if profile_image is None:
        return None
    if profile_image.content_type not in PROFILE_IMAGE_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="Profile image must be a PNG or JPEG")

    image_content = await profile_image.read(MAX_PROFILE_IMAGE_BYTES + 1)
    if len(image_content) > MAX_PROFILE_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Profile image must be at most 2 MB")
    if not image_content:
        raise HTTPException(status_code=422, detail="Profile image cannot be empty")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(image_content)) as image:
                if image.format not in PROFILE_IMAGE_FORMATS:
                    raise HTTPException(status_code=415, detail="Profile image must be a PNG or JPEG")
                if image.width * image.height > MAX_PROFILE_IMAGE_PIXELS:
                    raise HTTPException(status_code=422, detail="Profile image has too many pixels")
                image.verify()
    except HTTPException:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid profile image")

    return image_content


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.post("/register", response_model=UserRegisterResponse)
@limiter.limit("5/hour")  # Máximo 5 registros por hora por IP
async def register_user(
    request: Request,
    background_tasks: BackgroundTasks,
    username: Annotated[str, Form(min_length=3, max_length=24)],
    email: Annotated[EmailStr, Form()],
    password: Annotated[str, Form(min_length=8, max_length=128)],
    full_name: Annotated[Optional[str], Form(max_length=120)] = None,
    profile_image: Annotated[Optional[UploadFile], File()] = None,
    db: Session = Depends(get_db)
):
    logger.info("Nueva solicitud de registro")
    username = username.strip()
    email = str(email).strip().lower()
    consume_auth_attempt(db, scope="register", subject=email, client_ip=get_client_ip(request), maximum=5, window_seconds=3600)
    full_name = full_name.strip() if full_name else None
    if len(username) < 3:
        raise HTTPException(status_code=422, detail="Username must contain at least 3 non-space characters")
    # bcrypt procesa como máximo 72 bytes; rechazar evita truncamientos silenciosos.
    if len(password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be at most 72 bytes")
    if register_login.check_username_exist(db, username):  # <-- NUEVO
        raise HTTPException(status_code=400, detail="Username already taken")
    db_user = register_login.check_user_exist(db, email)
    image_content = await read_valid_profile_image(profile_image)

    if db_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")
    
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

    # En desarrollo local puede no haber SMTP configurado; el alta no debe
    # fallar por eso. En producción se envía la verificación normalmente.
    if SMTP_SERVER and SMTP_PORT and SENDER_EMAIL and SENDER_PASSWORD and VERIFICATION_LINK:
        verification_token = create_email_verification_token(email)
        background_tasks.add_task(send_verification_email, email, verification_token, name)
    else:
        logger.warning("Registro creado sin correo de verificación: SMTP no configurado")
    return new_user


def create_email_verification_token(email: str):
    expire = datetime.now(timezone.utc) + timedelta(hours=1)  # Token válido por 1 hora
    to_encode = {"sub": email, "exp": expire}
    token = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return token


def create_password_reset_token(email: str, hashed_password: str) -> str:
    """Crea un enlace de un solo uso efectivo para recuperar una contraseña.

    La huella de la contraseña actual invalida automáticamente cualquier
    enlace anterior cuando el usuario completa un restablecimiento.
    """
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    fingerprint = sha256(hashed_password.encode()).hexdigest()
    return jwt.encode(
        {"sub": email, "purpose": "password_reset", "password_fingerprint": fingerprint, "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )


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
        with smtplib.SMTP(smtp_server, smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, email, msg.as_string())
    except Exception:
        logger.exception("No se pudo enviar el correo de verificación")


def send_password_reset_email(email: str, reset_link: str, name: str):
    subject = "Restablece tu contraseña de Banderas, países y regiones"
    body = f"""
    Hola {name},

    Recibimos una solicitud para restablecer la contraseña de tu cuenta.

    Elige una contraseña nueva desde este enlace (válido durante una hora):

    {reset_link}

    Si no solicitaste el cambio, puedes ignorar este correo. Tu contraseña no cambiará.

    Saludos,
    El equipo de Banderas, países y regiones
    """
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = email
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=SMTP_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, email, msg.as_string())
    except Exception:
        logger.exception("No se pudo enviar el correo de recuperación")


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
@limiter.limit("3/hour")  # Máximo 3 reenvíos por hora por IP
def resend_verification_email(
    request: Request,
    background_tasks: BackgroundTasks,
    email: Annotated[EmailStr, Body()],
    db: Session = Depends(get_db)
):
    normalized_email = str(email).strip().lower()
    consume_auth_attempt(db, scope="resend", subject=normalized_email, client_ip=get_client_ip(request), maximum=3, window_seconds=3600)
    user = db.query(models.User).filter(models.User.email == normalized_email).first()
    if user and not user.is_verified:
        if SMTP_SERVER and SMTP_PORT and SENDER_EMAIL and SENDER_PASSWORD and VERIFICATION_LINK:
            verification_token = create_email_verification_token(user.email)
            name = user.full_name if user.full_name else user.username
            background_tasks.add_task(send_verification_email, user.email, verification_token, name)
        else:
            logger.warning("No se reenvió la verificación: SMTP no configurado")

    # Respuesta uniforme para no revelar si el correo tiene una cuenta.
    return {"msg": "If the account requires verification, an email was sent"}


@app.post("/password-reset/request")
@limiter.limit("3/hour")
def request_password_reset(
    request: Request,
    background_tasks: BackgroundTasks,
    email: Annotated[EmailStr, Body()],
    db: Session = Depends(get_db),
):
    normalized_email = str(email).strip().lower()
    consume_auth_attempt(db, scope="password_reset", subject=normalized_email, client_ip=get_client_ip(request), maximum=3, window_seconds=3600)
    user = db.query(models.User).filter(models.User.email == normalized_email).first()
    if user and SMTP_SERVER and SMTP_PORT and SENDER_EMAIL and SENDER_PASSWORD:
        token = create_password_reset_token(user.email, user.hashed_password)
        reset_link = f"{str(request.base_url).rstrip('/')}/reset-password?token={quote(token, safe='')}"
        name = user.full_name or user.username
        background_tasks.add_task(send_password_reset_email, user.email, reset_link, name)
    elif user:
        logger.warning("No se envió recuperación: SMTP no configurado")

    # La misma respuesta para cuentas existentes e inexistentes evita enumeración.
    return {"msg": "If the account exists, a password reset email was sent"}


@app.post("/password-reset/confirm")
@limiter.limit("5/hour")
def confirm_password_reset(
    request: Request,
    payload: user_schema.PasswordResetConfirm,
    db: Session = Depends(get_db),
):
    if len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(status_code=422, detail="Password must be at most 72 bytes")
    try:
        claims = jwt.decode(payload.token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=400, detail="Password reset link expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid password reset link")

    email = claims.get("sub")
    fingerprint = claims.get("password_fingerprint")
    if claims.get("purpose") != "password_reset" or not isinstance(email, str) or not isinstance(fingerprint, str):
        raise HTTPException(status_code=400, detail="Invalid password reset link")

    consume_auth_attempt(db, scope="password_reset_confirm", subject=email, client_ip=get_client_ip(request), maximum=5, window_seconds=3600)
    user = db.query(models.User).filter(models.User.email == email.lower()).first()
    if not user or not compare_digest(fingerprint, sha256(user.hashed_password.encode()).hexdigest()):
        raise HTTPException(status_code=400, detail="Password reset link is no longer valid")

    user.hashed_password = get_password_hash(payload.password)
    revoke_all_refresh_sessions(db, user.id)
    db.commit()
    return {"msg": "Password updated"}


@app.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
def password_reset_page():
    return """<!doctype html><html lang=\"es\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Restablecer contraseña</title><style>body{font-family:system-ui;max-width:28rem;margin:8vh auto;padding:1.5rem;color:#172033}label,input,button{display:block;width:100%;box-sizing:border-box}input,button{padding:.8rem;margin:.5rem 0 1rem}button{background:#166534;border:0;border-radius:.5rem;color:white;font-weight:700}#status{min-height:1.5rem}</style><main><h1>Restablecer contraseña</h1><p>Elige una contraseña nueva para tu cuenta.</p><form id=\"reset-form\"><label>Nueva contraseña<input id=\"password\" type=\"password\" minlength=\"8\" maxlength=\"72\" required autocomplete=\"new-password\"></label><button>Guardar contraseña</button></form><p id=\"status\" role=\"status\"></p></main><script>const token=new URLSearchParams(location.search).get('token');const form=document.getElementById('reset-form');const status=document.getElementById('status');if(!token){form.hidden=true;status.textContent='El enlace de recuperación no es válido.'}form.addEventListener('submit',async e=>{e.preventDefault();status.textContent='Guardando…';const response=await fetch('/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,password:document.getElementById('password').value})});const result=await response.json().catch(()=>({}));status.textContent=response.ok?'Contraseña actualizada. Ya puedes volver a la app e iniciar sesión.':(result.detail||'No se pudo cambiar la contraseña.');if(response.ok)form.hidden=true})</script></html>"""


def verify_password(plain_password, hashed_password):
    try:
        return password_hash.verify(plain_password, hashed_password)
    except (UnknownHashError, ValueError):
        return False


def get_password_hash(password):
    return password_hash.hash(password)


def get_user(db, username_or_email: str):
    user = register_login.get_user_by_username(db, username_or_email)
    if not user:
        user = db.query(models.User).filter(models.User.email == username_or_email).first()
    return user



def authenticate_user(username: str, password: str, db: Session):
    user = get_user(db, username)
    if not user:
        return False
    try:
        valid, updated_hash = password_hash.verify_and_update(password, user.hashed_password)
    except (UnknownHashError, ValueError):
        return False
    if not valid:
        return False
    if updated_hash:
        user.hashed_password = updated_hash
        db.commit()
    return user


def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    if "sub" in to_encode:
        to_encode["sub"] = str(to_encode["sub"])
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _refresh_token_hash(refresh_token: str) -> str:
    return sha256(refresh_token.encode("utf-8")).hexdigest()


def revoke_all_refresh_sessions(db: Session, user_id: int) -> None:
    """Revoca sesiones persistentes al cambiar contraseña o cerrar una cuenta."""
    db.query(models.AuthRefreshSession).filter(
        models.AuthRefreshSession.user_id == user_id,
        models.AuthRefreshSession.revoked_at.is_(None),
    ).update({models.AuthRefreshSession.revoked_at: _utc_now_naive()}, synchronize_session=False)


def issue_token_pair(db: Session, user: models.User, family_id: str | None = None) -> dict[str, str | int]:
    refresh_token = token_urlsafe(48)
    session = models.AuthRefreshSession(
        id=str(uuid4()),
        user_id=user.id,
        family_id=family_id or str(uuid4()),
        token_hash=_refresh_token_hash(refresh_token),
        expires_at=_utc_now_naive() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
    )
    db.add(session)
    db.commit()
    access_token = create_access_token(
        data={"sub": user.id, "purpose": "access"},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": int(ACCESS_TOKEN_EXPIRE_MINUTES * 60),
    }


def rotate_refresh_token(db: Session, refresh_token: str) -> dict[str, str | int]:
    """Rota un refresh opaco y revoca su familia si se reutiliza uno viejo."""
    record = db.query(models.AuthRefreshSession).with_for_update().filter(
        models.AuthRefreshSession.token_hash == _refresh_token_hash(refresh_token)
    ).first()
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Refresh token inválido o vencido",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if not record:
        raise credentials_exception

    now = _utc_now_naive()
    if record.revoked_at or record.expires_at <= now:
        db.query(models.AuthRefreshSession).filter(
            models.AuthRefreshSession.user_id == record.user_id,
            models.AuthRefreshSession.family_id == record.family_id,
            models.AuthRefreshSession.revoked_at.is_(None),
        ).update({models.AuthRefreshSession.revoked_at: now}, synchronize_session=False)
        db.commit()
        raise credentials_exception

    user = record.user
    if not user or not user.is_active or not user.is_verified:
        record.revoked_at = now
        db.commit()
        raise credentials_exception

    record.revoked_at = now
    replacement = issue_token_pair(db, user, family_id=record.family_id)
    replacement_record = db.query(models.AuthRefreshSession).filter(
        models.AuthRefreshSession.token_hash == _refresh_token_hash(str(replacement["refresh_token"]))
    ).one()
    record.replaced_by = replacement_record.id
    db.commit()
    return replacement



async def get_current_user(user_token: Annotated[str, Depends(oauth2_scheme)], db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(user_token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        try:
            user_id = int(sub)  # <- castear
        except (TypeError, ValueError):
            raise credentials_exception
    except ExpiredSignatureError:
        raise credentials_exception
    except (InvalidSignatureError, DecodeError, InvalidTokenError):
        raise credentials_exception

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: Annotated[user_schema.User, Depends(get_current_user)], ):
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


@app.post("/login")
@limiter.limit("10/minute")  # Máximo 10 intentos por minuto por IP
async def login_for_access_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db)
):
    consume_auth_attempt(db, scope="login", subject=form_data.username, client_ip=get_client_ip(request), maximum=10, window_seconds=60)
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        logger.warning("Intento de login fallido")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail={"message": "Usuario o contraseña incorrectos"},
                            headers={"WWW-Authenticate": "Bearer"}, )
    
    logger.info("Login exitoso")
    if user and not user.is_verified:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail={"message": "Usuario no verificado", "email": user.email},
                            headers={"WWW-Authenticate": "Bearer"})
    session_tokens = issue_token_pair(db, user)
    full_name = user.full_name if user.full_name else user.username
    profile_image_url = f"/user/{user.id}/profile_image"
    return {**session_tokens, "full_name": full_name, "profile_image_url": profile_image_url, "user_id": user.id}

#@app.post("/refresh")
# def refresh(response: Response, refresh_token: Optional[str] = Cookie(None)):
#     payload = jwt.decode(refresh_token, SECRET_KEY, algorithms=[ALGORITHM])
#     if payload.get("type") != "refresh": raise HTTPException(401)
#     jti = payload["jti"]
#     rec = db.get_refresh(jti)
#     if not rec or rec.revoked: 
#         # Reutilización detectada → revocar familia
#         revoke_family(jti)
#         raise HTTPException(401, "refresh reuse")
#     # Rotación
#     rec.revoked = True
#     new_jti = uuid4().hex
#     new_rt = create_refresh(rec.user_id, new_jti)
#     rec.replaced_by = new_jti
#     db.save_refresh(new_jti, rec.user_id, ua=..., ip=...)
#     new_at = create_access(rec.user_id)
#     response.set_cookie("refresh_token", new_rt, httponly=True, secure=True, samesite="Lax", path="/refresh")
#     return {"access_token": new_at, "token_type": "bearer"}


@app.post("/token", response_model=token.Token, tags=["auth"])
@limiter.limit("10/minute")  # Máximo 10 intentos por minuto
async def issue_token(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: Session = Depends(get_db)
):
    consume_auth_attempt(db, scope="token", subject=form_data.username, client_ip=get_client_ip(request), maximum=10, window_seconds=60)
    user = authenticate_user(form_data.username, form_data.password, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"message": "Usuario no verificado", "email": user.email},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return issue_token_pair(db, user)


@app.post("/token/refresh", response_model=token.Token, tags=["auth"])
@limiter.limit("30/minute")
async def refresh_access_token(
    request: Request,
    payload: token.RefreshTokenRequest,
    db: Session = Depends(get_db),
):
    return rotate_refresh_token(db, payload.refresh_token)


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


@app.get("/users/me", response_model=user_schema.UserMeResponse)
async def read_users_me(current_user: Annotated[user_schema.User, Depends(get_current_active_user)]):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "username": current_user.username,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "country": current_user.country,
        "ranking_alias": current_user.ranking_alias,
        "ranking_region": current_user.ranking_region,
        "profile_image_url": f"/user/{current_user.id}/profile_image",
        "onboarding_completed": current_user.onboarding_completed,
    }


@app.put("/users/me/onboarding")
@limiter.limit("30/minute")
async def update_onboarding(
    request: Request,
    onboarding_data: user_schema.OnboardingUpdate,
    current_user: Annotated[user_schema.User, Depends(get_current_active_user)],
    db: Session = Depends(get_db)
):
    try:
        updated_user = register_login.update_onboarding_status(
            db, 
            current_user.id, 
            onboarding_data.onboarding_completed
        )
        return {"message": "Onboarding status updated successfully", "onboarding_completed": updated_user.onboarding_completed}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.put("/user/profile", response_model=user_schema.UserRegisterResponse)
@limiter.limit("10/minute")
async def update_user_profile(request: Request, username: Annotated[str, Form(min_length=3, max_length=24)], full_name: Annotated[Optional[str], Form(max_length=120)], profile_image: Annotated[Optional[UploadFile], File()], country: Annotated[str, Form(max_length=80)], current_user: Annotated[user_schema.User, Depends(get_current_active_user)], delete_current_profile_image: Annotated[bool, Form()] = False, db: Session = Depends(get_db)):
    profile_image_bytes = await read_valid_profile_image(profile_image)
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
    # Ruta heredada: nunca permitir que el ID de la URL cambie el principal
    # autorizado. Los clientes nuevos deben consultar /users/me.
    if user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only read your own profile")
    user_profile = register_login.get_user_profile(db, current_user.id)
    if not user_profile:
        raise HTTPException(status_code=404, detail="User not found")
    return user_profile

@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok"}
