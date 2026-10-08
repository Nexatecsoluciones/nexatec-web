# Resolución de tenant por hostname (NEXATEC ERP Cloud)

Arquitectura de subdominios por cliente descrita en el pedido de
**NEXATEC ERP Cloud**. Ya resuelve tenant por hostname end-to-end a nivel
HTTP (ver corte 4), pero **todavía no está expuesta en Cloudflare ni
usada por ningún frontend** -- ver "Qué falta" más abajo antes de asumir
que ya hay subdominios funcionando de cara al público.

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

## Qué se conectó después (corte 4)

- `GET /api/public/hostname-context`: dado el hostname real de la request
  (`X-Forwarded-Host`, seteado por el BFF de Next.js en
  `apps/web/src/app/api/[...path]/route.ts` a partir del `Host` que llega
  de Cloudflare; cae a `Host` directo solo para dev/tests sin BFF), devuelve
  `{tenant_slug, system_slug, environment}` o `404 "Hostname no
  reconocido."` genérico (nunca distingue invalido/reservado/inexistente).
  Pensado para que el frontend sepa, ANTES de cualquier login, a que
  tenant corresponde el subdominio -- sin esto, el frontend seguiria
  sirviendo siempre la misma UI sin importar el hostname.
- `app/services/hostname_resolution.py::resolve_request_hostname` /
  `public_host_from_request`: la funcion que hace esto, reusable para
  cualquier otro endpoint/dependency futuro que necesite resolver tenant
  por hostname (no solo este).
- Tests en `tests/test_public_hostname.py` (8 casos): via
  `X-Forwarded-Host`, via `Host` directo, prioridad de
  `X-Forwarded-Host` sobre `Host` cuando vienen los dos, y 404 generico
  para host vacio/reservado/inexistente/hostil.
- **Todavia no usado por ningun frontend.** El endpoint existe y esta
  probado, pero `apps/web` no lo llama -- no hay todavia una pagina que
  cambie de contenido segun el subdominio. Ese es el proximo paso del
  lado de Next.js.

## Qué falta (no inventar que ya funciona)

1. **Exposición real en Cloudflare.** Hoy el túnel solo tiene un Public
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
2. Falta el modelo de ciclo de vida de demo (`EXPIRING`/`EXPIRED`/etc.)
   más fino que el `ProvisioningStatus`/`SystemAccessStatus` actuales --
   decidir si un hostname sigue respondiendo (con que contenido: aviso de
   "demo vencida") después de vencida la demo, en vez de simplemente
   dejar de aparecer.
