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

## Qué NO existe todavía

- Ningún endpoint de API ni pantalla usa estas tablas.
- Movimientos de stock, ventas, compras, CxC/CxP, contabilidad.
- Seed de la empresa demo ficticia.
