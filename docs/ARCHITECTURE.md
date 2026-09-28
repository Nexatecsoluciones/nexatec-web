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
| Almacenamiento de objetos | MinIO (S3-compatible) | Se añade en FASE 5, no antes — no se instala hasta que haya un flujo real de subida de archivos que lo necesite. |
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

## 5. Estado de este documento

Este archivo se actualiza en cada fase. Última actualización: FASE 2 (arquitectura
backend, control plane, autenticación).
