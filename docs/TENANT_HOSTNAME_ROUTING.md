# Resolución de tenant por hostname (NEXATEC ERP Cloud)

Primer corte de la arquitectura de subdominios por cliente descrita en el
pedido de **NEXATEC ERP Cloud**. Esto es la base de resolución, todavía
**no está conectada a ningún router HTTP ni a Cloudflare** -- ver
"Qué falta" más abajo antes de asumir que ya hay subdominios funcionando.

## Qué existe

- Tabla `tenant_hostnames` (control plane): `tenant_id` + `system_id` +
  `environment` (`DEMO`/`PRODUCTION`) + `hostname` único. Es el único
  lugar que puede decidir "este hostname es de este tenant".
- `app/services/hostname_resolution.py`:
  - `normalize_host(raw_host)`: sanea el `Host` header (minúsculas, saca
    puerto, valida forma de hostname DNS). Cualquier cosa que no sea un
    hostname bien formado devuelve `None` sin excepción -- headers
    spoofeados, SQL/paths/null bytes, strings gigantes, etc.
  - `resolve_hostname(db, raw_host)`: `None` si el host es inválido, está
    en `RESERVED_HOSTNAMES` (apex, `www`, `admin`, `api`, `staging`,
    `portal`, `app`, `static`, `mail`, `localhost`), o no está registrado.
    Nunca distingue en la respuesta "inválido" de "válido pero sin
    tenant" -- eso lo decide el router que lo use, siempre con un 404
    genérico, para no permitir enumerar subdominios.
  - `register_tenant_hostname(...)`: valida forma + reservados + duplicado
    antes de insertar. Pensado para ser llamado desde el job de
    provisioning de demo/producción (todavía no está conectado ahí).
- Tests (`tests/test_hostname_resolution.py`, 30 casos): resolución
  correcta, hosts reservados, hosts inválidos/hostiles (host-header
  injection), aislamiento cruzado entre dos tenants con el mismo sistema.

## Qué falta (no inventar que ya funciona)

1. **Conectar esto a un router real.** Hoy ningún endpoint usa
   `resolve_hostname`; la resolución de tenant en producción sigue siendo
   por sesión (`tenant_id` del usuario logueado), que sigue funcionando
   igual que antes. Falta decidir en qué capa se usa (middleware de
   FastAPI, o en el BFF de Next.js antes de llegar a la API) y agregar los
   tests de "host desconocido devuelve 404 controlado" a nivel HTTP.
2. **Exposición real en Cloudflare.** Hoy el túnel solo tiene un Public
   Hostname (`staging.nexatecpy.com`, ver `docs/CLOUDFLARE.md`). Un
   hostname nuevo en `tenant_hostnames` no significa que Cloudflare lo
   esté enrutando -- falta decidir entre (a) agregar cada hostname al
   túnel vía la API de Cloudflare cuando se aprueba una demo/producción
   (requiere un API Token con permisos acotados a la zona
   `nexatecpy.com`, que el propietario tiene que generar), o (b) un
   hostname wildcard (`*.nexatecpy.com`) -- no probado todavía, no asumir
   que funciona sin verificarlo primero.
3. **`register_tenant_hostname` no está invocado desde ningún job.** El
   wizard de creación de demo (Control Center) todavía no asigna un
   hostname automáticamente.
4. Falta el modelo de ciclo de vida de demo (`EXPIRING`/`EXPIRED`/etc.)
   más fino que el `ProvisioningStatus` actual -- lo que decide si un
   hostname sigue sirviendo contenido después de vencida la demo.
