# NEXATEC API (FastAPI)

Backend del control plane de la plataforma NEXATEC. Ver `../../docs/ARCHITECTURE.md`
para el razonamiento detras del stack y `../../docs/INITIAL_AUDIT.md` para el
estado del entorno del servidor.

## Desarrollo local

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Completar .env: password del rol de base de datos (ver mas abajo) y
# generar NEXATEC_SESSION_SECRET con: openssl rand -hex 32
```

### Base de datos (una sola vez, en el servidor)

El backend usa la instancia nativa de PostgreSQL ya existente en el
servidor (no un contenedor nuevo — ver `docs/ARCHITECTURE.md` seccion 2).
Como `postgres`:

```sql
CREATE ROLE nexatec_app LOGIN PASSWORD '...';
CREATE DATABASE nexatec_control OWNER nexatec_app;
\c nexatec_control
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER ROLE nexatec_app NOSUPERUSER NOCREATEDB NOCREATEROLE;
```

Luego aplicar las migraciones:

```bash
alembic upgrade head
```

### Levantar la API

```bash
uvicorn app.main:app --host 127.0.0.1 --port "${NEXATEC_API_PORT:-4301}"
```

Escucha solo en `127.0.0.1` — la exposicion hacia Internet se hace via
Nginx/Cloudflare Tunnel (FASE 7), nunca exponiendo el puerto directamente.

### Tests

```bash
python -m pytest tests/ -v
```

Los tests de `tests/test_auth_flow.py` corren contra `nexatec_control` ya
migrada y limpian sus propios datos al terminar (no requieren una base de
tests separada todavia — eso se formaliza en FASE 8).

## Endpoints disponibles (FASE 2)

- `GET /api/health` — liveness, no requiere DB.
- `GET /api/ready` — readiness, valida conexion a PostgreSQL.
- `POST /api/auth/login` — email + password + `turnstile_token`. Bloqueo
  progresivo tras intentos fallidos, mensaje generico (no revela si el
  email existe).
- `POST /api/auth/logout` — revoca la sesion en base de datos (no solo
  borra la cookie).
- `GET /api/auth/me` — usuario autenticado actual.
- `POST /api/auth/password-reset/request` y `/confirm` — flujo de
  recuperacion con token de un solo uso y expiracion corta (30 min). El
  envio real del email queda pendiente de FASE de proveedor de correo.
