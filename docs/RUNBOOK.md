# Runbook

## Servicios (staging)

| Servicio | Que es |
|---|---|
| `nexatec-api-staging.service` | FastAPI, `127.0.0.1:4301` |
| `nexatec-web-staging.service` | Next.js standalone, `127.0.0.1:4302` |
| `cloudflared` | Tunnel hacia Cloudflare (instalado por `cloudflared service install`, fuera del control de este repo) |
| `nexatec-garage.service` | Garage, storage S3-compatible, `127.0.0.1:3900` (S3) / `127.0.0.1:3901` (RPC) |
| `nexatec-backup.timer` | Diario 03:30: backup cifrado de `nexatec_control` + `nxt_*` (ver `docs/BACKUPS.md`) |
| `nexatec-restore-check.timer` | Domingos 05:00: restaura el ultimo backup en una base temporal y lo verifica |
| `nexatec-sweep-expired-demos.timer` | Cada 15 min, corre `python -m app.cli sweep-expired-demos` (oneshot `nexatec-sweep-expired-demos.service`) |

Ninguno depende de mantener una sesion SSH abierta (`systemd`, `enabled`,
sobreviven a un `restart` individual y a boot).

## Iniciar / detener / reiniciar

```bash
sudo systemctl start   nexatec-api-staging.service
sudo systemctl stop    nexatec-api-staging.service
sudo systemctl restart nexatec-api-staging.service

sudo systemctl start   nexatec-web-staging.service
sudo systemctl stop    nexatec-web-staging.service
sudo systemctl restart nexatec-web-staging.service

sudo systemctl status cloudflared --no-pager
sudo systemctl restart cloudflared   # solo si hace falta

sudo systemctl restart nexatec-garage.service
```

Administracion de Garage (buckets, claves) via su propia CLI, usando el
config fuera del repo:

```bash
export GARAGE_CONFIG=/etc/nexatec/garage.toml
garage status
garage bucket list
garage bucket info nexatec-media
```

**Nunca** reiniciar el servidor completo para esto -- aloja tambien
`hesed-ot-sistema` (ver `docs/INITIAL_AUDIT.md`). Reiniciar servicios
individuales no lo afecta.

## Ver logs

```bash
sudo journalctl -u nexatec-api-staging.service -f       # tail en vivo
sudo journalctl -u nexatec-web-staging.service -n 100 --no-pager
sudo journalctl -u cloudflared -n 100 --no-pager
```

## Comprobar health

```bash
curl -s http://127.0.0.1:4301/api/health   # liveness API, sin DB
curl -s http://127.0.0.1:4301/api/ready    # valida conexion a PostgreSQL
curl -s http://127.0.0.1:4302              # web
curl -s http://127.0.0.1:4302/api/health   # BFF -> API (mismo resultado que el primero)
```

Publico (una vez el Tunnel + Access esten activos):

```bash
curl -sI https://nexatecpy.com          # Cloudflare Access deberia interceptar (redirect a login de Access)
```

## Comprobar el Tunnel

```bash
sudo systemctl status cloudflared --no-pager
sudo journalctl -u cloudflared -n 50 --no-pager | grep -i "connect\|error"
```

En el dashboard: Zero Trust → Networking → Tunnels → `nexatec-platform` →
deberia mostrar **HEALTHY** con al menos una conexion activa.

## Deshabilitar staging en emergencia

Sin tocar `hesed_ot` ni el resto del servidor:

```bash
# Opcion 1: apagar la app (Cloudflare Access seguiria respondiendo 502/503)
sudo systemctl stop nexatec-web-staging.service
sudo systemctl stop nexatec-api-staging.service

# Opcion 2 (mas fuerte): cortar el Tunnel completo
sudo systemctl stop cloudflared
```

Para restaurar: `start` en el orden inverso (`cloudflared` no depende de
que las apps esten arriba, pero conviene levantar API antes que Web).

## Barrido de demos vencidas

`nexatec-sweep-expired-demos.timer` corre cada 15 minutos y marca
`EXPIRED` cualquier `SystemAccess` de entorno `DEMO` cuyo `expires_at` ya
paso (ver `sweep_expired_demos` en `app/routers/demos.py`). Esto NO es lo
que bloquea el acceso -- `app/routers/portal.py` ya rechaza acceso aunque
este timer nunca haya corrido, comparando `expires_at` en cada request.
El timer solo mantiene prolijo el estado que ve el admin (y lo que
devuelve `/portal/my-systems`) sin esperar a que alguien intente entrar.

```bash
sudo systemctl status nexatec-sweep-expired-demos.timer --no-pager
sudo systemctl start  nexatec-sweep-expired-demos.service   # disparo manual
sudo journalctl -u nexatec-sweep-expired-demos.service -n 20 --no-pager
```

Las unidades (`/etc/systemd/system/nexatec-sweep-expired-demos.{service,timer}`)
no estan versionadas en el repo (igual que las otras, ver arriba);
`apps/api/scripts/sweep-expired-demos.sh` es el script que ejecutan.

## Rotar secrets

Los `.env` reales de staging viven en `/etc/nexatec/*.env` (fuera del
repo). Para rotar, por ejemplo, `NEXATEC_SESSION_SECRET`:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
# editar /etc/nexatec/api-staging.env con el nuevo valor
sudo systemctl restart nexatec-api-staging.service
```

Rotar esto invalida todas las sesiones activas (los tokens firmados con
la clave vieja dejan de validar).

## Actualizar codigo

Ver `docs/DEPLOYMENT.md` seccion "Deploy de un cambio de codigo".

## Revisar migraciones

```bash
cd apps/api && source .venv/bin/activate
set -a; source /etc/nexatec/api-staging.env; set +a
alembic current
alembic history
```

## Backups

Diarios, cifrados, con prueba de restauracion semanal: ver `docs/BACKUPS.md`.
Pendiente: copia fuera del servidor y guardar la clave fuera del servidor.

## Revocar acceso de un usuario / cerrar una demo comprometida

Ver endpoints de `apps/api/app/routers/`:

- Revocar todas las sesiones de un usuario: cambiar su password (endpoint
  admin todavia no existe para esto especificamente; hoy se hace via
  `revoke_all_sessions_for_user` llamado desde password-reset -- para un
  admin forzar esto sin que el usuario pida reset, hay que hacerlo
  manualmente contra la DB por ahora).
- Suspender un acceso: `POST /api/admin/demos/{id}/suspend` (demo) o
  `PATCH /api/admin/system-access/{id}` con `status: SUSPENDED` (cualquier
  entitlement).
