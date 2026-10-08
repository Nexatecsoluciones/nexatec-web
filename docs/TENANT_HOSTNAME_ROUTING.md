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

## Qué se conectó después (corte 3)

- `POST /api/admin/demos` asigna automáticamente `demo-<tenant.slug>.nexatecpy.com`
  al dejar la demo en `READY` (best-effort: si el slug no es válido como
  DNS o hay colisión, el `ValueError` de `register_tenant_hostname` se
  atrapa y la demo sigue funcionando sin hostname -- nunca bloquea el
  aprovisionamiento).
- `POST /api/admin/demos/{id}/convert-to-production` asigna
  `<tenant.slug>.nexatecpy.com` (sin el prefijo `demo-`) a la instancia de
  producción nueva, con la misma lógica best-effort.
- Ambos endpoints devuelven el campo `hostname` en la respuesta
  (`null` si no se pudo asignar). Ver
  `test_convert_demo_to_production_provisions_and_assigns_hostname` y la
  aserción agregada en `test_demo_full_lifecycle_provisions_real_isolated_database`
  (`tests/test_tenancy.py`).

## Qué falta (no inventar que ya funciona)

1. **Conectar esto a un router HTTP público.** Lo de arriba asigna el
   hostname en la tabla, pero ningún endpoint todavía RESUELVE tenant a
   partir del `Host` header de una request real; la resolución de tenant
   en producción sigue siendo por sesión (`tenant_id` del usuario
   logueado). Falta decidir en qué capa se usa esto (middleware de
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
   que funciona sin verificarlo primero. Hasta que esto exista, el
   hostname que devuelve la API es un dato guardado, no una URL que
   realmente resuelva en internet.
3. Falta el modelo de ciclo de vida de demo (`EXPIRING`/`EXPIRED`/etc.)
   más fino que el `ProvisioningStatus`/`SystemAccessStatus` actuales --
   decidir si un hostname sigue respondiendo (con que contenido: aviso de
   "demo vencida") después de vencida la demo, en vez de simplemente
   dejar de aparecer.
