# Arquitectura — Plataforma NEXATEC

Este documento explica las decisiones de stack y las adaptaciones hechas frente
al servidor real disponible (ver `docs/INITIAL_AUDIT.md` para el detalle del
entorno). Se actualiza a medida que avanzan las fases.

## 1. Stack elegido

| Capa | Elección | Por qué |
|---|---|---|
| Frontend público / portal | Next.js + TypeScript + React | SSR/SSG para el sitio público (SEO, velocidad), y mismo framework para portal de clientes y admin. Se migra gradualmente desde el HTML estático de FASE 1 (FASE 3 en adelante), sin romper lo ya publicado. |
| Backend / API | FastAPI (Python 3.12) + Pydantic + SQLAlchemy 2.x + Alembic | Tipado fuerte con Pydantic (valida entrada, evita mass assignment), async nativo, Alembic da migraciones controladas (expand/migrate/contract), y el equipo puede razonar sobre RBAC/multi-tenant explícitamente en cada endpoint en vez de vía convención de ORM. |
| Base de datos | PostgreSQL 16 (ya instalado de forma nativa en el servidor) | Ver sección 2: se reutiliza la instancia nativa existente en vez de contenedores adicionales. |
| Cache / colas | Valkey | Requisito del usuario: alternativa 100% open-source a Redis, mismo protocolo. |
| Almacenamiento de objetos | Garage (S3-compatible) | Ver nota abajo: MinIO Community Server quedo archivado/sin mantenimiento, se reemplazo por una alternativa activa. |
| Reverse proxy / exposición | Nginx existente + Cloudflare Tunnel (a configurar en FASE 7) | El servidor no tiene Cloudflare Tunnel activo todavía; se documenta y prepara en FASE 7, no se inventa una configuración de un tunnel inexistente. |

No se usa Node.js para el backend: FastAPI + SQLAlchemy + Alembic dan control
más explícito sobre validación de entrada, RBAC por endpoint, y migraciones,
que es justamente donde este proyecto tiene más riesgo (multi-tenancy,
pagos, aislamiento de datos). Next.js sigue siendo la elección para todo lo
que es UI.

## 2. Adaptación crítica: PostgreSQL — una instancia nativa, no tres clusters

El pedido original describía `postgres_control`, `postgres_demo` y
`postgres_prod` como **instancias/contenedores separados**. Esto no es viable
tal cual en el servidor real disponible ahora mismo:

- El servidor ya corre PostgreSQL 16 **nativo** (no en contenedor), usado en
  producción por `hesed_ot` (otro sistema, de otro cliente). No se toca esa
  base ni ese rol.
- Solo hay **8.8 GB libres en disco** y Docker ni siquiera está instalado
  todavía. Levantar 3 clusters PostgreSQL adicionales en contenedores (cada
  uno con su propio `PGDATA`, WAL, shared_buffers, etc.) en esta máquina
  competiría por RAM/disco con un sistema de producción de otro cliente que
  ya está sirviendo tráfico real.

**Decisión**: se reutiliza la misma instancia nativa de PostgreSQL 16 para
todo, con **aislamiento lógico** en vez de aislamiento por proceso:

- Una base `nexatec_control` para el control plane (tenants, users, roles,
  systems, subscriptions, pagos, auditoría, etc.).
- Un rol de aplicación `nexatec_app` **sin** `SUPERUSER`, `CREATEDB` ni
  `CREATEROLE`, con privilegios solo sobre `nexatec_control`.
- Cuando se implemente el aprovisionamiento de demo/producción (FASE 4), cada
  tenant+sistema+entorno obtiene su **propia base de datos** dentro de la
  misma instancia (`nxt_<tenant>_<sistema>_demo`, `..._prod`), cada una con su
  propio rol de mínimo privilegio — el mismo modelo "database per tenant"
  pedido, solo que sobre un único servidor PostgreSQL en vez de tres.
- `tenant_database_registry` (tabla del control plane) guarda, por tenant y
  entorno, el host/puerto/nombre de base real. Esto es lo que permite migrar
  después un tenant grande a una instancia PostgreSQL dedicada **sin tocar
  código de aplicación**: solo se actualiza el registro y las credenciales de
  conexión de ese tenant. El diseño escala hacia la arquitectura original
  (instancias separadas, incluso servidores separados) cambiando
  configuración, no reescribiendo la aplicación.
- Backups: al ser una sola instancia nativa, se usa `pg_dump`/`pg_basebackup`
  programado (ver `docs/BACKUPS.md`, a crear en FASE 8) con retención
  diferenciada por prefijo de base (`nxt_..._demo` vs `nxt_..._prod` vs
  `nexatec_control`).

Esta decisión se revisa cuando exista un servidor dedicado o cuando el
volumen de un cliente lo justifique.

## 3. Puertos

Todos los puertos son configurables por variable de entorno, ninguno
hardcodeado, y todos los servicios de aplicación escuchan solo en
`127.0.0.1` (nunca `0.0.0.0`) hasta que exista un mecanismo de exposición
controlado (Nginx interno o Cloudflare Tunnel).

| Variable | Servicio | Valor de desarrollo |
|---|---|---|
| `NEXATEC_API_PORT` | FastAPI (uvicorn) | 4301 |
| `NEXATEC_WEB_PORT` | Next.js (cuando exista, FASE 3+) | 4302 |
| `NEXATEC_VALKEY_PORT` | Valkey (cuando exista, dentro de red Docker privada) | 4303 |

Puertos ya ocupados en el servidor y **no reutilizados**: `22`, `80`, `443`,
`111`, `3000` (hesed-ot-sistema), `5432` (PostgreSQL nativo), `4330`/`44321`
(agente de monitoreo Oracle Cloud), más puertos efímeros de VS Code Server.

## 4. Multi-tenancy y RBAC (FASE 2, en curso)

Roles definidos en `apps/api/app/security/rbac.py`:
`SUPER_ADMIN`, `ADMIN`, `SUPPORT`, `BILLING`, `CLIENT_ADMIN`, `CLIENT_USER`,
`DEMO_USER`. La autorización se resuelve **siempre server-side** vía
dependencias de FastAPI (`Depends`), nunca confiando en el frontend.

Autenticación: contraseñas con Argon2id (`argon2-cffi`), sesiones server-side
(no JWT en localStorage), cookies `Secure`, `HttpOnly`, `SameSite=Lax`.
Detalle completo en `docs/SECURITY.md` (a expandir en FASE 7).

## 5. `apps/web/AGENTS.md` y `apps/web/CLAUDE.md`

Verificado en FASE 4 (no se asumio, se comprobo en el codigo fuente): estos
archivos los genera **Next.js 16 mismo**, no Claude ni ningun agente externo.
El generador vive en `node_modules/next/dist/server/lib/generate-agent-files.js`
(y en `create-next-app/helpers/generate-agent-files.ts`): cuando `next dev`
detecta un agente de codigo IA en el entorno, escribe un bloque estatico
delimitado por marcadores (`<!-- BEGIN:nextjs-agent-rules -->` / `END`) que
solo dice "esta version de Next.js puede tener cambios que rompen tu
conocimiento previo, revisa `node_modules/next/dist/docs/` antes de escribir
codigo". No contiene secretos, credenciales ni instrucciones que ejecuten
nada; es texto informativo. `CLAUDE.md` solo referencia `@AGENTS.md`.

Decision: se versionan. `next dev` los recrea igual en cada arranque si
faltan, asi que ignorarlos solo generaria diffs no deseados sin evitar su
existencia; commitearlos idénticos evita ese ruido.

## 6. Staging y same-origin BFF

Detalle completo en `docs/DEPLOYMENT.md` y `docs/CLOUDFLARE.md`. Resumen de
la decision: el navegador solo conoce `https://staging.nexatecpy.com`;
nunca `127.0.0.1:4301`/`4302`. Se evaluaron tres opciones para esto:

1. **`rewrites()` de `next.config.ts`** -- simple, pero opaco: no da
   control explicito sobre que headers se reenvian.
2. **Reverse proxy interno (nginx/Caddy) delante de Next.js** -- correcto,
   pero agrega un proceso mas que administrar y no reutiliza nada del
   codigo ya escrito.
3. **Route Handler catch-all (BFF) en Next.js** -- elegida. Vive en
   `apps/web/src/app/api/[...path]/route.ts`. Reenvia explicitamente
   `Host`, `X-Forwarded-Proto`, `X-Forwarded-For`/`CF-Connecting-IP` hacia
   `NEXATEC_INTERNAL_API_URL` (server-only). No puede convertirse en open
   proxy porque el destino esta hardcodeado a `/api/<path>` contra un host
   fijo, nunca un host que venga del request.

`is_production` en `app/core/config.py` se redefinio para cubrir tanto
`staging` como `production` (cookies `Secure`, ocultar `/docs`, HSTS);
el bypass de Turnstile en cambio exige exactamente
`NEXATEC_ENV=development`, nunca "no produccion" en general -- son
propiedades distintas a proposito, ver `docs/SECURITY.md`.

## 7. Storage (FASE 5): Garage en vez de MinIO

El plan original nombraba MinIO como preferencia. Al ir a instalarlo se
encontro que **MinIO Community Server esta archivado**: el servidor de
descargas oficial devuelve `410 Gone` con un aviso explicito ("no longer
maintained... does not provide security updates"). Instalar un storage
server sin actualizaciones de seguridad para guardar archivos de clientes
no es aceptable, asi que se eligio una alternativa activa:

**Garage** (`garagehq.deuxfleurs.fr`) -- binario unico en Rust, API S3
compatible, pensado especificamente para self-hosting de baja escala
(justo este caso), mantenido activamente. Corre como servicio nativo
(`nexatec-garage.service`, ver `docs/RUNBOOK.md`), escucha solo en
`127.0.0.1:3900` (S3 API) y `127.0.0.1:3901` (RPC interno del cluster),
nunca expuesto a Internet. Configurado como cluster de un solo nodo
(`replication_factor = 1`), con 3 GB de capacidad asignada (conservador
frente a los ~6-7 GB libres reales del servidor en el momento de
instalarlo).

Diseño de claves de objeto: `tenant-<hex8>/[demo|prod]/<system-hex8 o
"general">/<uuid>.<ext>` (`app/services/storage.py::build_object_key`) --
igual que las bases de datos de tenant, nunca se deriva de texto de
usuario. Los archivos nunca se sirven directo: `app/routers/media.py`
siempre devuelve una URL firmada de corta duracion (5 min), generada
recien despues de verificar membership del tenant (mismo mecanismo
anti-IDOR que el resto del sistema).

Validacion de archivos por contenido real (`app/services/file_validation.py`,
magic bytes -- nunca extension ni Content-Type declarado), imagenes
reescritas sin EXIF y con thumbnail generado (`app/services/image_processing.py`,
Pillow). Video/FFmpeg queda deliberadamente sin implementar -- no hay
todavia un flujo que lo necesite; se documenta como preparado, no como
hecho.

## 8. Estado de este documento

Este archivo se actualiza en cada fase. Última actualización: FASE 5
(storage con Garage).
