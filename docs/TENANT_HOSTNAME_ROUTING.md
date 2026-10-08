# Resolución de tenant por hostname (NEXATEC ERP Cloud)

Arquitectura de subdominios por cliente descrita en el pedido de
**NEXATEC ERP Cloud**. Resuelve tenant por hostname end-to-end a nivel
HTTP (corte 4), expone de verdad el subdominio en Cloudflare al crear una
demo/producción y lo retira al suspenderla/expirarla (corte 5 y 6) --
pero **todavía no la usa ningún frontend** -- ver "Qué falta" más abajo
antes de asumir que ya hay una experiencia de usuario por subdominio.

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

## Qué se conectó después (corte 5): exposición real en Cloudflare

- `app/services/cloudflare_dns.py`: `ensure_public_hostname_route(hostname)`
  crea (si falta) el registro DNS CNAME `hostname -> <tunnel_id>.cfargotunnel.com`
  (proxied) y agrega una regla al ingress del Tunnel `nexatec-platform`
  apuntando a `CLOUDFLARE_TUNNEL_SERVICE` (`http://127.0.0.1:4302`, el
  mismo Next.js que ya sirve `staging.nexatecpy.com`). Idempotente: si ya
  está todo como se espera, no hace ningún cambio; si encuentra un DNS
  record apuntando a otro lado, **no lo pisa**, tira error. La regla
  catch-all del tunnel (`http_status:404`) siempre queda última.
  `remove_public_hostname_route(hostname)` es el reverso (mismo chequeo de
  "no borrar lo que no creamos nosotros").
- `_assign_hostname_best_effort` (en `app/routers/demos.py`) llama a esto
  después de registrar el hostname en `tenant_hostnames` -- best-effort
  también: si Cloudflare no está configurado o falla, el hostname queda
  igual guardado en la tabla, pero sin exponer a internet todavía. Nunca
  bloquea la creación de la demo/producción.
- Credenciales en `/etc/nexatec/cloudflare-api.env` (fuera del repo, igual
  que el resto de secrets de staging) y mezcladas en
  `/etc/nexatec/api-staging.env` para que las lea el proceso real. Ver
  `.env.example` para los nombres exactos. El token es un API Token
  acotado (Zone.DNS:Edit + Account.Cloudflare Tunnel:Edit, scope solo a
  `nexatecpy.com`), nunca la Global API Key.
- Verificado end-to-end contra Cloudflare real (no solo mocks): se creó
  `cf-wiring-test.nexatecpy.com`, resolvió por DNS, respondió `200` por
  HTTPS a través del túnel, y se removió limpio después -- el túnel quedó
  exactamente como antes de la prueba.
- Tests en `tests/test_cloudflare_dns.py` (8 casos, con `httpx.MockTransport`,
  sin red real): alta, idempotencia, conflicto con DNS existente, error de
  API, y los mismos casos para el borrado.

## Qué se conectó después (corte 6): retirar el hostname al suspender/expirar

- `suspend_demo`, `expire_demo` y `sweep_expired_demos` (los tres en
  `app/routers/demos.py`) llaman a `_unexpose_hostname_best_effort`: saca
  la ruta de Cloudflare (DNS + ingress) del hostname asignado, pero
  **nunca borra la fila de `tenant_hostnames`** -- el subdominio sigue
  siendo de ese tenant, solo que no resuelve mientras el acceso no está
  activo.
- `renew_demo` llama a `_reexpose_hostname_best_effort`: si el acceso
  había sido suspendido/expirado (y por lo tanto el hostname sacado de
  Cloudflare), renovarlo lo vuelve a exponer con el mismo subdominio de
  siempre, no uno nuevo.
- Todo con el mismo criterio best-effort del resto: si Cloudflare no
  responde, no bloquea la operación de negocio (suspender/renovar/expirar
  sigue funcionando igual).
- Test `test_suspend_expire_renew_toggle_cloudflare_exposure` (en
  `tests/test_tenancy.py`) verifica, con `ensure/remove_public_hostname_route`
  mockeados, que cada transición llama exactamente a la función correcta
  con el hostname correcto -- crear -> `ensure`, suspender -> `remove`,
  renovar -> `ensure`, expirar -> `remove`.

## Qué se conectó después (corte 7)

- Lógica de exposición movida a `app/services/hostname_exposure.py`
  (`unexpose`, `reexpose`, `reexpose_active_only`), usada por los
  endpoints de demos y por el PATCH de tenant.
- `PATCH /api/admin/tenants/{id}` con cambio de `status`:
  `SUSPENDED`/`ARCHIVED` retira de Cloudflare TODOS los hostnames del
  tenant; volver a `ACTIVE` re-expone solo los que tienen su
  `SystemAccess` en `ACTIVE` (una demo que venció mientras el tenant
  estaba suspendido no vuelve a resolver).
- El read-modify-write del ingress del túnel está serializado con
  `pg_advisory_lock` (clave fija en `cloudflare_dns._TUNNEL_INGRESS_LOCK_KEY`)
  -- funciona entre la API y el timer de barrido, que son procesos
  distintos. Test `test_tunnel_ingress_lock_blocks_other_connections`.

## Decisiones tomadas (no son pendientes)

- Al convertir demo -> producción, el hostname de DEMO **sigue vivo** junto
  al de producción. Es deliberado: el cliente puede seguir usando la demo
  durante la transición. Si se quiere cortar, el admin suspende la demo
  (`POST /api/admin/demos/{id}/suspend`), que ya retira su hostname.

## Qué falta (no inventar que ya funciona)

1. Ningún frontend consume `GET /api/public/hostname-context` todavía: un
   subdominio de tenant hoy muestra la misma web pública que staging.
2. No hay borrado físico de tenant (solo `ARCHIVED`), así que tampoco hay
   purga de filas de `tenant_hostnames`; las rutas de Cloudflare sí se
   retiran al archivar.
