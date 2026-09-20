# Despliegue de la API de Atlas de Banderas

## Requisitos

- Python 3.12 y las dependencias de `requirements-prod.txt`.
- PostgreSQL con copias de seguridad.
- Terminación TLS/HTTPS en el proveedor o proxy.
- Servicio SMTP capaz de enviar verificaciones.

## Configuración

Copiar `.env.example` como `.env` únicamente en el servidor y completar:

- `ENV=production`
- `SECRET_KEY`: valor aleatorio de al menos 32 caracteres.
- `DATABASE_URL`: conexión `postgresql+psycopg2://...` con credenciales no incluidas en Git.
- `ALLOWED_ORIGINS`: lista mínima; para la app Capacitor incluir `https://localhost`.
- `BASE_URL`: URL HTTPS del frontend que recibe la confirmación de correo.
- `DAILY_MAX_ATTEMPTS`: entre 3 y 6; el valor recomendado es 4.
- SMTP y `VERIFICATION_LINK`: todos obligatorios en producción.

La aplicación se niega a iniciar en producción si detecta wildcard CORS, HTTP, secreto débil, base no PostgreSQL o correo incompleto.

## Despliegue

Con un entorno Python administrado por el proveedor:

```bash
python -m venv venv
venv/bin/pip install -r requirements-prod.txt
venv/bin/alembic upgrade head
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --proxy-headers
```

O con el `Dockerfile` incluido:

```bash
docker build -t atlas-api .
docker run --rm --env-file .env atlas-api alembic upgrade head
docker run --rm --env-file .env -p 8000:8000 atlas-api
```

Usar el gestor de procesos del proveedor en vez de ejecutar Uvicorn manualmente. Aplicar las migraciones una sola vez antes de reemplazar las instancias de la API; no usar `create_all` en producción.

## Comprobaciones

```bash
# En desarrollo, instalar requirements.txt antes de ejecutar las pruebas.
venv/bin/python -m pytest
curl --fail https://API_PUBLICA/health/live
curl --fail https://API_PUBLICA/health/ready
```

Después del despliegue, crear una cuenta de prueba, verificar el correo, iniciar sesión, publicar una etapa, consultar el ranking y eliminar la cuenta. Confirmar que el usuario y sus resultados desaparezcan.

Configurar además:

- alertas de errores y disponibilidad sin registrar contraseñas, tokens ni correos completos;
- límites y rotación de logs;
- backup automático de PostgreSQL y una prueba de restauración;
- actualización de dependencias y revisión periódica de vulnerabilidades;
- secretos en el almacén del proveedor, nunca en `.env` versionado.
