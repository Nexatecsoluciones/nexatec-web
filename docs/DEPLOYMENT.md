# Deployment

Estado actual: **staging**, en el mismo servidor OCI que aloja `hesed-ot-sistema`
(ver `docs/INITIAL_AUDIT.md`). No hay entorno de produccion todavia.

## Arquitectura de despliegue

```
Usuario
  |  HTTPS
  v
staging.nexatecpy.com  (Cloudflare)
  |  Cloudflare Access (autenticacion previa)
  v
Cloudflare Tunnel (cloudflared, conexion saliente desde el servidor)
  |
  v
127.0.0.1:4302  (Next.js standalone, systemd: nexatec-web-staging.service)
  |  same-origin BFF: src/app/api/[...path]/route.ts
  v
127.0.0.1:4301  (FastAPI/uvicorn, systemd: nexatec-api-staging.service)
  |
  v
PostgreSQL nativo (127.0.0.1:5432) -- nexatec_control + DBs de tenant
```

Nada de esto abre puertos nuevos hacia Internet: `cloudflared` es una
conexion **saliente** desde el servidor hacia el borde de Cloudflare, no
port-forwarding entrante. `127.0.0.1:4301` y `127.0.0.1:4302` nunca son
alcanzables desde fuera del servidor.

## Componentes y donde viven

| Componente | Puerto | Bind | Servicio |
|---|---|---|---|
| FastAPI (API) | `NEXATEC_API_PORT` (4301) | `127.0.0.1` | `nexatec-api-staging.service` |
| Next.js (web, standalone) | `NEXATEC_WEB_PORT` (4302) | `127.0.0.1` | `nexatec-web-staging.service` |
| cloudflared | (saliente, sin puerto local publicado) | — | unidad propia creada por `cloudflared service install` |
| PostgreSQL | 5432 | `127.0.0.1` | nativo, sin cambios (preexistente) |

## Build de la web (Next.js standalone)

Next.js NO corre con `next dev` ni `next start` en staging/produccion:
usa `output: "standalone"` (ver `apps/web/next.config.ts`) para producir un
build autocontenido.

```bash
cd apps/web
./scripts/build-standalone.sh
```

Ese script corre `npm run build` y copia `public/` + `.next/static/` dentro
de `.next/standalone/` (paso manual requerido por Next.js para standalone,
ver su documentacion). El resultado se ejecuta con:

```bash
node .next/standalone/server.js
```

Los valores `NEXT_PUBLIC_*` (por ejemplo la site key de Turnstile) quedan
**horneados en el bundle en tiempo de build**, tomados de
`apps/web/.env.production.local` (no versionado). Cambiar uno de esos
valores requiere reconstruir, no solo reiniciar el servicio.

## Variables de entorno

Ver `apps/api/.env.example` y `apps/web/.env.example`. En staging/produccion
viven en `EnvironmentFile` de systemd, **fuera del repositorio**:

- `/etc/nexatec/api-staging.env`
- `/etc/nexatec/web-staging.env`

`NEXATEC_ENV` (staging/production) es independiente de `NODE_ENV`:
`NODE_ENV` siempre es `production` en cualquier despliegue real (controla
optimizaciones de Next.js); `NEXATEC_ENV` es el que la API usa para decidir
seguridad (cookies `Secure`, si se puede omitir Turnstile, etc -- ver
`docs/SECURITY.md`).

## Same-origin BFF (`/api/*`)

El navegador solo conoce `https://staging.nexatecpy.com/api/...`. Nunca ve
`127.0.0.1:4301`. El proxy vive en
`apps/web/src/app/api/[...path]/route.ts`: reenvia server-side hacia
`NEXATEC_INTERNAL_API_URL` (variable server-only, nunca `NEXT_PUBLIC_`).
No es un proxy abierto -- el destino siempre es
`${NEXATEC_INTERNAL_API_URL}/api/<path capturado>`, nunca un host arbitrario.

## Deploy de un cambio de codigo (staging)

```bash
cd /home/opc/nexatec-web
git pull origin feature/nexatec-platform

# Backend
cd apps/api
source .venv/bin/activate
pip install -r requirements.txt
set -a; source /etc/nexatec/api-staging.env; set +a
sudo systemctl start nexatec-backup.service   # backup ANTES de migrar
alembic upgrade head                 # control plane (nexatec_control)
python -m app.cli migrate-tenants    # cada base de tenant, una por una
sudo systemctl restart nexatec-api-staging.service

# Frontend
cd ../web
npm install
./scripts/build-standalone.sh
sudo systemctl restart nexatec-web-staging.service
```

Ver `docs/RUNBOOK.md` para operacion dia a dia (logs, health, rollback).
