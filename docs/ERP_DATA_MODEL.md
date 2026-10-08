# Modelo de datos del ERP (bases de tenant)

Cada tenant+sistema+entorno tiene su propia base (`nxt_<tenant>_<sistema>_<demo|prod>`,
ver `docs/ARCHITECTURE.md`). Este documento cubre lo que vive **dentro** de
esas bases. El control plane (`nexatec_control`) está aparte y no se mezcla.

## Dos Alembic separados

| | Control plane | Bases de tenant |
|---|---|---|
| Metadata | `app.core.db.Base` | `app.tenant_models.TenantBase` |
| Config | `alembic.ini` + `alembic/` | `tenant_alembic.ini` + `tenant_alembic/` |
| Corre con | `nexatec_app` | el rol propio de cada tenant (dueño de su base) |
| Cuándo | `alembic upgrade head` en el deploy | al aprovisionar + `python -m app.cli migrate-tenants` en el deploy |

`tenant_alembic/env.py` no tiene URL fija: la conexión la pasa
`app/services/tenant_migrations.py`, ya autenticada con el rol del tenant,
así una migración no puede tocar otra base que no sea la suya.

### Generar una migración nueva de tenant (desarrollo)

Contra una base **descartable**, nunca contra una de cliente:

```bash
cd apps/api && source .venv/bin/activate && set -a && source .env && set +a
M="postgresql://$NEXATEC_PROVISIONER_DB_USER:$NEXATEC_PROVISIONER_DB_PASSWORD@127.0.0.1:5432/postgres"
psql "$M" -c "CREATE DATABASE nxt_scratch_autogen"
alembic -c tenant_alembic.ini \
  -x url="postgresql+psycopg://$NEXATEC_PROVISIONER_DB_USER:$NEXATEC_PROVISIONER_DB_PASSWORD@127.0.0.1:5432/nxt_scratch_autogen" \
  upgrade head
alembic -c tenant_alembic.ini \
  -x url="postgresql+psycopg://$NEXATEC_PROVISIONER_DB_USER:$NEXATEC_PROVISIONER_DB_PASSWORD@127.0.0.1:5432/nxt_scratch_autogen" \
  revision --autogenerate -m "descripcion"
psql "$M" -c "DROP DATABASE nxt_scratch_autogen"
```

## Provisioning

`provision_tenant_database` migra la base a head **antes** de devolverla.
Si la migración falla, la base queda `FAILED` (nunca `READY` con esquema a
medias) y la demo/producción no se activa. Una base `READY` creada antes de
que existieran estas migraciones (`schema_version = "0"`) se lleva a head
la próxima vez que se pide.

## Revisión `43663f4ef304` — maestros del núcleo

| Tabla | Qué es | Reglas en la base (no solo en la app) |
|---|---|---|
| `company` | La empresa (una sola fila: la base ES la empresa) | `CHECK id = 1`; `ruc_is_fictitious` para demos |
| `currencies` | PYG (0 decimales), USD (2) | — |
| `branches` | Sucursales; `establishment_code` guardado pero sin usar hasta habilitar SIFEN | `code` único |
| `warehouses` | Depósitos por sucursal | `code` único, FK a sucursal |
| `units_of_measure` | UN, KG, LT, MT, HR, SRV | `code` único |
| `taxes` | IVA10 / IVA5 / EXENTA, **versionados por `valid_from`** | tasa 0–100; `(code, valid_from)` único |
| `product_categories` | Árbol de categorías | nombre único |
| `products` | Bienes y servicios | `sku` único; precio y costo `>= 0`; dinero en `NUMERIC` |
| `parties` | Clientes y/o proveedores en un solo maestro | debe ser cliente o proveedor; RUC único; límite de crédito y plazo `>= 0` |

`parties.ruc_status` distingue `UNVERIFIED` / `FORMAT_OK` / `VERIFIED_PROVIDER`
/ `FICTITIOUS`: un RUC con formato válido **no** se considera verificado.

Las tasas de IVA son datos (no constantes en código) y deben revisarse con
un contador antes de producción. `valid_from = 1900-01-01` marca "carga
inicial", no una fecha legal.

## API de maestros (`/api/erp/{system_access_id}/...`)

`app/routers/erp_masters.py`, sobre la base resuelta por
`app/security/erp_context.py`:

- Se exige **membresía activa real** en el tenant. El personal de NEXATEC
  (incluido SUPER_ADMIN) no tiene acceso operacional implícito: si soporte
  necesita entrar, se lo agrega como miembro y queda auditado.
- Tenant `ACTIVE`, acceso `ACTIVE` y no vencido (se compara `expires_at` en
  cada request, aunque el timer de barrido no haya corrido).
- Lectura: cualquier miembro. Escritura: `CLIENT_ADMIN`.
- Un no-miembro recibe 404 (no se confirma que el recurso exista).
- Toda escritura queda en `audit_logs` del control plane.

| Recurso | Operaciones |
|---|---|
| `currencies`, `units`, `taxes` (vigentes hoy) | GET |
| `company` | GET, PUT (RUC validado por DV; la empresa demo no admite un RUC real) |
| `branches`, `warehouses` | GET, POST, PATCH |
| `product-categories` | GET, POST |
| `products` | GET (búsqueda `q`, `active`, paginado `limit`≤200/`offset`), GET por id, POST, PATCH |
| `parties` | GET (búsqueda, `role=customer|supplier`, paginado), GET por id, POST, PATCH |

RUC: `app/services/ruc.py` valida solo el **formato** (DV módulo 11, mismo
algoritmo que `python-stdnum`, probado con sus vectores publicados). Un RUC
con DV correcto queda `FORMAT_OK`, nunca `VERIFIED_PROVIDER`.

## Revisión `13e7ce183686` — inventario

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `stock_movements` | Libro de movimientos (kardex) | **Inmutable**: un trigger rechaza todo `UPDATE`/`DELETE`; cantidad `> 0`; `direction` ±1; costo `>= 0`; `reverses_movement_id` único (cada movimiento se revierte una sola vez) |
| `stock_balances` | Saldo por producto + depósito | `on_hand >= 0` (stock negativo prohibido); `0 <= reserved <= on_hand` |

Lógica en `app/services/inventory.py`, API en `/api/erp/{system_access_id}/stock/...`:

| Endpoint | Qué hace |
|---|---|
| `POST receipts` | Entrada con costo; recalcula el **promedio ponderado** del producto |
| `POST issues` | Salida al costo promedio vigente (queda congelado en el movimiento) |
| `POST adjustments` | Ajuste IN/OUT, **motivo obligatorio** |
| `POST transfers` | Dos movimientos (OUT/IN) con el mismo `group_id`; no cambia el promedio |
| `POST movements/{id}/reverse` | Movimiento compensatorio (motivo obligatorio). Si es una pata de transferencia, revierte las dos. No deja revertir una entrada cuyo stock ya se usó, ni una reversión |
| `GET movements` | Kardex filtrable (producto, depósito, fechas), paginado |
| `GET balances` | Saldos con disponible, costo promedio y valorización |

- **Concurrencia**: lock del producto primero, después los saldos en orden
  de depósito. Test con 10 hilos sacando 3 de un stock de 20: pasan
  exactamente 6, saldo final 2.
- **Idempotencia**: header `Idempotency-Key`; un reintento devuelve el mismo
  movimiento sin volver a descontar.
- Servicios (`tracks_stock = false`) y productos/depósitos inactivos no
  admiten movimientos.
- Escribe solo `CLIENT_ADMIN`; todo queda auditado.

## Empresa demo ficticia (`app/services/demo_seed.py`)

Al aprovisionar una base de **DEMO** (nunca PRODUCTION) se carga, en una
sola transacción y de forma idempotente:

- `NEXATEC Empresa Demo S.A. — SIMULACIÓN`, con `ruc_is_fictitious = true`.
- 1 sucursal (establecimiento `001`), 2 depósitos, 4 categorías.
- 12 productos/servicios con precios y costos plausibles en guaraníes
  (IVA 10% y 5% según el rubro); los servicios no manejan stock.
- 6 clientes, 3 proveedores y 1 mixto, todos con nombres de fantasía
  evidentes y emails `@ejemplo.invalid`.
- Todos los RUC en el rango `999xxxxx` con DV válido y estado
  `FICTITIOUS` (las personas jurídicas reales arrancan en 80.000.000).

Si la carga falla, la base queda `FAILED` y la demo no se activa. Las bases
de producción arrancan **vacías**.

## Qué NO existe todavía

- Pantallas (frontend) para estos maestros.
- Reservas de stock por pedido (el campo `reserved` existe, nadie lo usa todavía), lotes/series/vencimientos, FIFO.
- Ventas, compras, CxC/CxP, contabilidad.
- Historia transaccional en la demo (ventas/compras de 3-6 meses): depende de que existan esos módulos.
- Matriz de permisos granular (hoy solo CLIENT_ADMIN escribe / CLIENT_USER lee).
