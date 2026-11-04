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
- `BASE_URL`: URL HTTPS canónica de la ficha pública del juego (por ejemplo,
  `https://ofiul.com/games/banderas-paises-y-regiones`). Recibe la confirmación
  de correo y genera enlaces públicos compartidos.
- `RACE_MODE_ENABLED`: mantener en `false` hasta aplicar la migración y validar
  Carrera con dos dispositivos; habilitarlo exige un solo proceso ASGI.
- `RACE_INVITE_BASE_URL`: URL HTTPS pública de esta API, usada por `/race/<token>`.
- `ANDROID_APP_LINK_SHA256_CERT_FINGERPRINT`: huella de Play App Signing que
  publica `/.well-known/assetlinks.json`.
- `DAILY_MAX_ATTEMPTS`: entre 3 y 6; el valor recomendado es 4.
- `RATE_LIMIT_STORAGE_URI`: opcional. Una URI de Redis o Key Value permite que
  los límites sobrevivan reinicios y se compartan entre instancias. Para el
  lanzamiento inicial puede omitirse y usar memoria local; usar `rediss://`
  si la conexión no viaja por red privada.
- SMTP y `VERIFICATION_LINK`: todos obligatorios en producción. Si el proveedor
  usa un nombre de usuario distinto del remitente (por ejemplo, Resend), definir
  también `SMTP_USERNAME`; si se omite, se usa `SENDER_EMAIL` por compatibilidad.

La aplicación se niega a iniciar en producción si detecta wildcard CORS, HTTP, secreto débil, base no PostgreSQL o correo incompleto.

## Staging de Carrera

La preparación reproducible está documentada junto a la app en
`mobile/STAGING.md`. En este repositorio:

1. copiar `.env.staging.example` como las variables secretas del servicio;
2. ejecutar `python scripts/validate_staging_config.py` durante el predeploy;
3. ejecutar `alembic upgrade head` antes de iniciar la nueva versión;
4. mantener exactamente una instancia y un worker;
5. ejecutar `python scripts/smoke_race_staging.py` desde una máquina externa
   con dos cuentas verificadas.

El smoke test recorre salud, `assetlinks.json`, login, sala, dos WebSockets,
ready, las 12 respuestas y el ganador. Las credenciales se reciben únicamente
mediante variables de entorno y no se imprimen.

### Actualizar la regla de cierre por podio

El cierre de Carrera lo decide esta API. Publicar un APK nuevo por sí solo no
activa la regla de podio: también hay que integrar y desplegar el backend con
`RACE_RULESET_VERSION = 2`. Este cambio no requiere migraciones de base de datos.

Después del despliegue, iniciar una ronda nueva y comprobar que el snapshot
devuelve `current_round.ruleset_version = 2`. Las rondas ya iniciadas con la
versión 1 conservan el cierre con la primera llegada.

- Con 2 participantes, la primera llegada cierra la ronda.
- Con 3 participantes, la primera llegada mantiene la ronda activa y la segunda
  la cierra; la persona restante queda tercera.
- Con 4–8 participantes, la tercera llegada cierra la ronda.
- El límite original de 90 segundos continúa vigente; al vencer, se conservan
  los puestos de quienes llegaron y se clasifica al resto por avance.

No usar solo el smoke test de dos cuentas para validar este cambio: ese caso
mantiene el mismo comportamiento en ambas versiones. Probar también una ronda
de tres participantes y confirmar que la primera llegada recibe `answer_result`
y progreso `12`, sin emitir `race_finished` hasta la segunda llegada o el plazo.

## Despliegue

Con un entorno Python administrado por el proveedor:

```bash
python -m venv venv
venv/bin/pip install -r requirements-prod.txt
venv/bin/alembic upgrade head
venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
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

## RLS de PostgreSQL

La aplicación móvil no se conecta directamente a PostgreSQL: toda autorización
se realiza en la API mediante el usuario autenticado. No activar RLS sobre las
tablas existentes sin antes separar el rol de migraciones del rol de ejecución
y establecer el ID autenticado dentro de cada transacción; hacerlo con el rol
actual bloquearía rutas públicas y de autenticación. Si se añade RLS, debe
probarse primero en una base de staging con políticas por tabla y una conexión
de aplicación sin privilegio `BYPASSRLS`.
