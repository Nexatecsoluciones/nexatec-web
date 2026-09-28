# Seguridad

Este documento resume los controles implementados y donde vive cada uno.
No se declara "100% seguro" en ningun punto -- ver "Riesgos pendientes" al
final de cada fase en el historial de commits y en `docs/RUNBOOK.md`.

## Autenticacion

- Passwords: Argon2id (`app/security/passwords.py`), politica minima de
  12 caracteres + letra + numero.
- Sesiones server-side revocables (`user_sessions`, no JWT en
  localStorage): login rota la sesion, logout revoca en DB (no solo borra
  la cookie), cambiar la password invalida todas las sesiones activas.
- Cookie de sesion: `HttpOnly`, `SameSite=Lax`, `Secure` cuando
  `NEXATEC_ENV` es `staging` o `production` (ver `app/core/config.py`,
  propiedad `is_production` -- el nombre quedo de FASE 2, pero hoy cubre
  ambos). **No** se usa `Domain=.nexatecpy.com`: la cookie es host-only
  para `staging.nexatecpy.com`, que es lo que corresponde mientras
  produccion no exista en el mismo dominio con necesidad real de
  compartir sesion entre subdominios.
- Bloqueo progresivo ante fuerza bruta (intentos fallidos consecutivos).
- Mensajes de error genericos en login/reset (no revelan si un email
  existe).
- Reset de password: token de un solo uso, hash SHA-256 en DB, TTL 30 min.

## Turnstile (anti-bot)

Verificacion SIEMPRE server-side (`app/security/turnstile.py`), llamando
al endpoint real de Cloudflare. El bypass de desarrollo depende
EXCLUSIVAMENTE de `NEXATEC_ENV=development` -- nunca de "no produccion" en
general, y nunca de nada que el cliente pueda enviar en el request. En
`staging`/`production` sin secret key configurada, la verificacion
simplemente falla (fail-closed), no se omite. Detalle de las claves de
prueba usadas hoy en staging: `docs/CLOUDFLARE.md`.

## RBAC

7 roles fijos (`app/security/roles.py`): `SUPER_ADMIN`, `ADMIN`,
`SUPPORT`, `BILLING` (globales, sin tenant) y `CLIENT_ADMIN`,
`CLIENT_USER`, `DEMO_USER` (dentro de un tenant, via `TenantUser` --
FASE 4). La autorizacion se resuelve siempre server-side
(`require_roles`/`require_admin_panel`), nunca se confia en lo que
decide el frontend.

## Aislamiento multi-tenant / anti-IDOR

- Ningun endpoint acepta un `tenant_id` del cliente y confia en el:
  `app/security/tenancy_rbac.py::assert_tenant_membership` verifica
  membership real contra `TenantUser` en cada acceso a un recurso
  tenant-scoped.
- Ante un recurso de otro tenant: `404` (no `403`) -- no se confirma que
  el recurso exista.
- Test automatizado obligatorio (`tests/test_tenancy.py`): Tenant B nunca
  puede leer ni accionar sobre un `system_access` de Tenant A, ni con el
  UUID real ni con uno inventado. Verificado tambien manualmente con
  `curl` real (no solo tests) en FASE 4.
- Nombres de base de datos/rol de PostgreSQL derivados UNICAMENTE de
  hex(UUID), nunca de texto de usuario (`app/services/db_naming.py`,
  15 tests con inputs maliciosos).
- Passwords de DBs de tenant cifradas en reposo (Fernet), en tabla
  separada nunca serializada por ningun endpoint.

## Media / almacenamiento de archivos (FASE 5)

- Validacion por contenido real (magic bytes), nunca por extension ni por
  el `Content-Type` que declara el cliente (`app/services/file_validation.py`).
- Limite de tamaño por upload (10 MB hoy, configurable).
- Imagenes reescritas sin metadata EXIF (puede contener geolocalizacion u
  otros datos del dispositivo de origen) y con thumbnail generado
  server-side -- nunca se confia en dimensiones declaradas por el cliente.
- Archivos nunca servidos directo desde la API ni desde una URL publica
  fija: siempre una URL firmada de Garage (S3) con TTL corto (5 min),
  generada solo despues de verificar membership del tenant sobre ese
  recurso especifico -- mismo mecanismo anti-IDOR que el resto del
  sistema. Un Tenant A no puede leer ni borrar un archivo de Tenant B (ver
  test dedicado en `tests/test_media.py`).
- Claves de objeto derivadas de UUIDs, nunca del nombre de archivo
  original del usuario (evita path traversal y colisiones).
- Garage escucha solo en `127.0.0.1`, nunca expuesto a Internet ni
  siquiera via el Tunnel.
- Antivirus (ClamAV) sobre uploads: NO implementado todavia, queda
  preparado como mejora futura (ver `docs/ARCHITECTURE.md`).

## Provisioning / PostgreSQL

- Rol de aplicacion `nexatec_app`: sin `SUPERUSER`, `CREATEDB` ni
  `CREATEROLE`.
- Rol `nexatec_provisioner`: separado, con `CREATEDB`+`CREATEROLE`
  (necesario para su unica funcion), tampoco `SUPERUSER`. Usado
  EXCLUSIVAMENTE por `app/services/provisioning.py`.
- PostgreSQL escucha solo en `127.0.0.1`, sin cambios de
  `listen_addresses`/`pg_hba.conf` (no se toco la config existente del
  servidor).
- Nunca se ejecuta `DROP DATABASE` desde un flujo de la aplicacion.

## Same-origin / exposicion de red (FASE staging)

- El navegador nunca conoce `127.0.0.1:4301` ni `4302`: todo pasa por
  `https://staging.nexatecpy.com`, con `/api/*` reenviado server-side por
  el BFF de Next.js (`apps/web/src/app/api/[...path]/route.ts`).
- `4301` y `4302` bindeados solo a `127.0.0.1` -- verificado con `ss -lntp`
  antes y despues de cada cambio.
- Cloudflare Tunnel es conexion saliente; no se abrio ningun puerto en
  `firewalld` ni en Security Lists/NSG de OCI.
- Cloudflare Access protege TODO `staging.nexatecpy.com` (incluido
  `/api/*`) como capa adicional -- no reemplaza el login/RBAC de NEXATEC.
- `CORS_ALLOWED_ORIGINS` nunca `*`; con el BFF same-origin, CORS pasa a
  ser defensa en profundidad (el trafico normal ya no depende de el).

## Secrets

- Ningun secreto real en el repositorio: `.env` reales excluidos por
  `.gitignore`, verificado que nunca estuvieron en el historial de git
  (`git log --all --full-history`).
- Secrets de staging en `EnvironmentFile` de systemd fuera del repo
  (`/etc/nexatec/*.env`, `chmod 600`), nunca en las unit files.
- Secret scan (`gitleaks`) corrido sobre todo el historial antes de cada
  push; un falso positivo (`docs_url=None`) documentado y excluido por
  fingerprint en `.gitleaksignore`, no silenciado a ciegas.

## Hardening del sistema (systemd + SELinux)

- `nexatec-api-staging.service` / `nexatec-web-staging.service`:
  `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
  `ProtectHome=read-only` (con `ReadWritePaths` acotado a su propio
  directorio), limites de memoria/CPU, `Restart=on-failure`.
- El servidor tiene SELinux en modo `Enforcing` (no se desactivo). El
  venv de Python necesito reetiquetarse a `bin_t` via
  `semanage fcontext` + `restorecon` para que systemd pudiera ejecutarlo
  (el contexto por defecto de archivos en `/home` es `user_home_t`, que
  systemd no puede ejecutar) -- es la forma correcta de resolverlo, no
  una excepcion a la politica.

## Riesgos pendientes (honesto, no exhaustivo)

- **`https://staging.nexatecpy.com` esta publico en Internet ahora
  mismo** -- Cloudflare Access todavia no esta configurado (decision
  consciente de posponerlo, ver `docs/CLOUDFLARE.md`). Cualquiera con la
  URL puede ver la app, incluida `/login` (aunque no puede entrar sin
  credenciales validas: el RBAC de NEXATEC sigue aplicando igual). Cerrar
  esto es la accion de seguridad pendiente mas importante hoy.
- Sin CSP ni el resto de security headers de FASE 7 todavia.
- Sin ClamAV / validacion de uploads (no hay uploads implementados aun).
- Turnstile en staging usa claves de prueba (ver `docs/CLOUDFLARE.md` para
  pasar a reales).
- Sin backups configurados (`docs/BACKUPS.md` pendiente).
- `nexatec_provisioner` es el rol de mayor privilegio del sistema
  (`CREATEDB`+`CREATEROLE`); su superficie de ataque es la API interna
  que lo usa, no un endpoint publico -- pero sigue siendo el punto de
  mayor cuidado.
