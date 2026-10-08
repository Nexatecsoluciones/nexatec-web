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

## Revisión `aee18fddad86` — pedidos de venta

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `document_sequences` | Correlativos por tipo de documento (`SALES_ORDER` → `OV-000001`) | Se toma con `FOR UPDATE` en la misma transacción: si falla la creación, el número no se consume (sin huecos) |
| `sales_orders` | Cabecera | Montos `>= 0`; número único |
| `sales_order_lines` | Líneas con tasa de IVA, montos y costo **congelados** | Cantidad `> 0`; descuento 0–100; `line_total = line_net + line_tax` |

Estados (`app/services/sales.py`, API en `/api/erp/{id}/sales/orders`):

```
DRAFT --confirm--> CONFIRMED --deliver--> DELIVERED
  |                   |
  +----cancel---------+--> CANCELLED (libera la reserva)
```

- **Confirmar** reserva stock de los bienes (`stock_balances.reserved`); si no
  alcanza, 409 y el pedido queda en borrador. La reserva impide que otra
  venta tome ese stock (test con 6 confirmaciones simultáneas de 3 sobre
  10: pasan 3).
- **Entregar** descuenta stock desde lo reservado (movimiento `ISSUE` con la
  referencia del pedido) y congela el costo unitario de cada línea (base del
  margen). Reintentar una entrega no vuelve a descontar.
- Un pedido **entregado no se cancela**: requiere devolución/nota de crédito
  (no implementado todavía).
- Las líneas solo se editan en borrador.
- **IVA incluido**: `bruto = cant × precio × (1 − desc%)`,
  `iva = bruto × tasa / (100 + tasa)`, `neto = bruto − iva`, redondeado a
  los decimales de la moneda (PYG: 0). Es cálculo de gestión; la
  liquidación tributaria la valida un contador y, para comprobantes
  electrónicos, SIFEN.

## Revisión `d654ac7086bf` — facturación interna, cobros y cuentas por cobrar

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `sales_invoices` | Comprobante **interno** de un pedido entregado (`FI-000001`) | **Gate fiscal**: `CHECK fiscal_status = 'INTERNAL_SIMULATION'`; desglose gravada 10 / IVA 10 / gravada 5 / IVA 5 / exenta que **tiene que sumar el total**; `0 <= balance_due <= total`; vencimiento ≥ emisión; una sola factura vigente por pedido (índice único parcial) |
| `customer_receipts` | Cobros (`RC-000001`) | Monto `> 0`; `0 <= unapplied_amount <= amount`; `Idempotency-Key` único |
| `receipt_allocations` | Aplicación de un cobro a facturas | Monto `> 0` |

**La factura NO es tributaria.** Cada respuesta trae
`legal_notice = "DOCUMENTO DE SIMULACIÓN — SIN VALIDEZ TRIBUTARIA"`. Para
emitir algo con validez (SIFEN/DNIT) hace falta: manual técnico vigente,
timbrado, certificado de firma, habilitación y homologación, y **una
migración explícita que levante `ck_sales_invoices_fiscal_gate`**. Ningún
código puede saltarse eso por error.

API (`app/services/receivables.py`):

| Endpoint | Qué hace |
|---|---|
| `POST sales/orders/{id}/invoice` | Factura un pedido **entregado**; vencimiento = emisión + plazo del cliente si es a crédito |
| `POST invoices/{id}/void` | Anula solo si no tiene cobros aplicados; después se puede re-facturar el pedido |
| `POST receipts` | Cobro con aplicación opcional; lo no aplicado queda como **anticipo** |
| `POST receipts/{id}/apply` | Aplica un anticipo a facturas |
| `POST receipts/{id}/void` | Anula el cobro y **restituye** los saldos de las facturas |
| `GET receivables/aging?as_of=` | Antigüedad por cliente: al día, 1–30, 31–60, 61–90, +90; anticipos y neto |
| `GET receivables/customers/{id}/statement` | Extracto con saldo corrido (lo anulado se muestra pero no suma) |

- **Sobreaplicación imposible**: lock de facturas en orden de id + `CHECK`
  en la base. Test con dos cobros simultáneos de 6.000 sobre un saldo de
  10.000: pasa uno, el otro es rechazado, saldo final 4.000.
- **Límite de crédito** al confirmar un pedido a crédito: deuda abierta +
  pedidos a crédito confirmados/entregados sin factura − anticipos +
  este pedido no puede superar el límite. Límite 0 = sin crédito. Lock del
  cliente para que dos pedidos simultáneos no usen el mismo margen.

## Revisión `8cb0b22fdea3` — compras y cuentas por pagar

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `purchase_orders` / `purchase_order_lines` | Orden de compra (`OC-000001`) | Recibido entre 0 y lo pedido; `line_total = line_net + line_tax` |
| `supplier_invoices` | Factura **del proveedor** (dato de tercero: su número y timbrado) | **Duplicado imposible**: único `(proveedor, número)` vigente; desglose suma el total; saldo entre 0 y total |
| `supplier_payments` / `supplier_payment_allocations` | Pagos (`PP-000001`) y su aplicación | Mismas reglas que los cobros |

Flujo (`app/services/purchases.py`, API en `/api/erp/{id}/...`):

- `purchases/orders`: crear (borrador) → `confirm` → `receive` (parcial o
  total, por línea) → queda `PARTIALLY_RECEIVED` o `RECEIVED`. `close`:
  `CANCELLED` si no se recibió nada, `CLOSED` si se recibió algo (corta lo
  pendiente). No se puede recibir más de lo pedido.
- La recepción mete stock al **costo neto de IVA** (`line_net / cantidad`):
  el IVA de compras es crédito fiscal, no costo. `Idempotency-Key` evita
  recibir dos veces (no aplica a recepciones de solo servicios, que no
  generan movimientos).
- `supplier-invoices`: se valida que el IVA declarado sea coherente con lo
  gravado (±1 unidad), y si la factura está ligada a una OC, que lo
  facturado no supere lo **efectivamente recibido** (control factura vs.
  recepción). Vencimiento por defecto = emisión + plazo del proveedor.
- `supplier-payments`: pago con aplicación, anticipo, `apply`, `void`
  (restituye saldos), sin sobrepago; `payables/aging` igual que cobranzas.

## Revisión `5e234767cef5` — contabilidad

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `accounts` | Plan de cuentas (agrupación / imputable) | Código único |
| `account_mappings` | Qué cuenta usa cada concepto automático (`AR`, `AP`, `INVENTORY`, `COGS`, `VAT_OUTPUT_10`…) | — |
| `fiscal_periods` | Mes contable `OPEN`/`CLOSED` | Único por año/mes |
| `journal_entries` / `journal_lines` | Asientos (`AS-000001`) | **Partida doble** verificada al COMMIT (trigger diferido: ≥2 líneas y debe = haber); **inmutables** (trigger); cada línea con un solo lado > 0; una reversión por asiento |

**Asientos automáticos** (`app/services/accounting.py`), siempre en la
**misma transacción** que la operación — si el asiento no se puede
registrar (período cerrado, cuenta sin mapear), la operación entera se
rechaza:

| Operación | Debe | Haber |
|---|---|---|
| Recepción de compra | Mercaderías | Mercaderías recibidas a facturar |
| Entrada/ajuste manual de stock | Mercaderías | Diferencias de inventario |
| Entrega de pedido de venta | Costo de mercaderías vendidas | Mercaderías |
| Salida/ajuste manual de stock | Diferencias de inventario | Mercaderías |
| Factura interna de venta | Deudores por ventas | Ventas (neto) + IVA débito 10% / 5% |
| Cobro | Caja (efectivo) o Bancos | Deudores (aplicado) + Anticipos de clientes (no aplicado) |
| Aplicación de anticipo de cliente | Anticipos de clientes | Deudores |
| Factura de proveedor | Mercaderías recibidas a facturar (con OC) o Gastos generales (sin OC) + IVA crédito fiscal | Proveedores |
| Pago a proveedor | Proveedores (aplicado) + Anticipos a proveedores (no aplicado) | Caja o Bancos |
| Anulaciones / reversión de stock | asiento de reversión del original | |

Transferencias entre depósitos no generan asiento (mismo activo).

Plan de cuentas inicial: 25 cuentas genéricas (1 Activo … 6 Gastos) y 16
mapeos. **Requiere revisión de un contador** antes de producción; se puede
ampliar (`POST accounting/accounts`) y re-mapear (`PUT accounting/mappings/{concepto}`).
No se puede desactivar una cuenta que esté mapeada.

API `/api/erp/{id}/accounting/...`: asientos manuales (balanceados, cuentas
imputables, período abierto) y su reversión — los automáticos **no** se
revierten a mano, se anula el documento de origen; libro diario
(`entries`), períodos (`close` / `reopen` con nota, auditado), y
**informes de gestión**: balance de comprobación, libro mayor por cuenta
con saldo corrido y paginado, estado de resultados, balance general (con
el resultado del ejercicio y verificación `activo = pasivo + patrimonio`).
Cada informe trae el aviso "no reemplaza libros rubricados ni la
liquidación tributaria".

Test de punta a punta con montos exactos: compra 10 × 11.000 IVA incl. →
pago → venta 4 × 22.000 → cobro de 100.000 aplicando 88.000. Resultado:
ventas 80.000, costo 40.000 (costo neto de IVA), resultado 40.000,
anticipo de cliente 12.000, balance cuadrado.

Limitación conocida: el costo de cada movimiento se redondea a la moneda
al contabilizarlo; el valor de inventario del kardex (4 decimales) y el de
la cuenta Mercaderías pueden diferir en unidades de guaraní.

## Revisión `0b4e58d9d0a6` — notas de crédito (devoluciones y bonificaciones)

| Tabla | Qué es | Reglas en la base |
|---|---|---|
| `sales_credit_notes` / `_lines` | Nota de crédito **interna** (`NC-000001`) sobre una factura vigente | Gate fiscal (`INTERNAL_SIMULATION`), desglose suma el total, `aplicado + a favor = total` |
| `sales_order_lines.quantity_returned` / `amount_credited` | Lo ya devuelto/acreditado por línea | **No se puede acreditar más de lo vendido** (`CHECK`) |

`POST /api/erp/{id}/invoices/{factura}/credit-notes`:

- `RETURN` (por cantidad, mismo precio/descuento/tasa de la venta). Con
  `restock`, los bienes vuelven al depósito **al costo congelado en la
  entrega** y se revierte el costo de ventas; sin `restock` (mercadería
  dañada) no vuelve stock. Devolver el último tramo acredita exactamente lo
  que queda de la línea (sin arrastrar redondeos).
- `DISCOUNT` (bonificación por monto bruto sobre una línea).
- El crédito baja primero el saldo de la factura; si ya estaba cobrada, el
  resto queda **a favor del cliente** (cuenta como anticipo en antigüedad,
  extracto y límite de crédito).
- Asiento: Debe Ventas + IVA débito / Haber Deudores (aplicado) + Anticipos
  de clientes (a favor). Reingreso: Debe Mercaderías / Haber Costo de ventas.
- Una factura con notas de crédito ya no se puede anular; el movimiento de
  reingreso no se puede revertir suelto.
- Pendiente: aplicar el saldo a favor de una nota de crédito a otra factura
  (hoy queda como anticipo visible) y devolución de dinero.

## Revisión `7c1e5a92d3f4` — CRM

- `crm_leads`: prospectos (todavía no son clientes). Exigen email o
  teléfono. `OPEN → CONVERTED | DISCARDED`. Convertir crea el `Party` cliente
  (sin RUC: se completa y valida en Terceros) y mueve al cliente las
  oportunidades y actividades del prospecto. No se descarta con oportunidades
  abiertas.
- `crm_opportunities` (`OP-000001`): de un cliente **o** de un prospecto
  (CHECK exactamente uno). Etapas `NEW → QUALIFIED → PROPOSAL → NEGOTIATION`
  con probabilidad sugerida 10/25/50/75 % (editable), y cierre `WON`
  (exige cliente, no prospecto, y monto > 0) o `LOST` (exige motivo, CHECK).
  Cerrada = inmutable por la API; `closed_at` presente sí y solo sí está
  cerrada (CHECK).
- `crm_activities`: llamada, reunión, email, WhatsApp, tarea, nota; ligada a
  cliente, prospecto u oportunidad (hereda el cliente/prospecto de la
  oportunidad). Una nota nace completada.
- `GET /crm/pipeline`: cantidad y monto por etapa, pronóstico ponderado
  (monto × probabilidad de las abiertas), ganado, tasa de cierre y actividades
  vencidas; `?mine=true` filtra por responsable.
- Permisos `crm:read` / `crm:write`: escriben Administrador, Gerencia y
  Ventas; convertir un prospecto exige además `parties:write`.
- La demo trae 3 prospectos, 6 oportunidades (4 abiertas, 1 ganada,
  1 perdida) y 4 actividades pendientes.

## Comprobantes imprimibles / PDF

`/imprimir/{acceso}/factura/{id}` y `/imprimir/{acceso}/nota-credito/{id}`:
vista A4 que el navegador guarda como PDF ("Imprimir → Guardar como PDF").
Lleva una **marca de agua diagonal que también se imprime** ("DOCUMENTO DE
SIMULACIÓN — SIN VALIDEZ TRIBUTARIA"), el aviso en recuadro rojo, "Sin
timbrado: no emitido ante la DNIT", y el RUC de una empresa demo rotulado
"FICTICIO — no es un RUC real". El E2E genera el PDF con Chromium y lo
verifica. No se agregó ninguna librería de PDF al servidor.

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

Además carga **operaciones** hechas con los mismos servicios del sistema
(no inserts directos), así stock, facturas, deudas y asientos son
consistentes por construcción:

- Aporte de capital (Caja 10M + Bancos 70M).
- 3 compras: bebidas (recibida, facturada, pagada 60%), almacén (recibida,
  facturada, pagada 100%), limpieza (recibida 50%, sin facturar).
- 5 ventas entregadas y facturadas (contado y crédito, cobradas total,
  parcial o nada), 1 pedido confirmado (stock reservado) y 1 en borrador.

Las operaciones quedan fechadas el día del aprovisionamiento (los servicios
no permiten registrar en el pasado); por eso la antigüedad de saldos de
una demo nueva arranca "al día".

Si la carga falla, la base queda `FAILED` y la demo no se activa. Las bases
de producción arrancan **vacías**.

## Qué NO existe todavía

- Lotes/series/vencimientos, FIFO.
- Integración SIFEN (bloqueada por diseño, ver gate fiscal).
- Retenciones de IVA/renta en pagos, costos de importación (landed cost), solicitudes y cotizaciones de compra.
- Cierre anual (traslado de resultados a Resultados acumulados), conciliación bancaria, centros de costo, multimoneda con diferencia de cambio.
- Historia de 3–6 meses en la demo (requiere permitir fechas pasadas de forma controlada solo en DEMO).
- CRM: campañas, embudos configurables por empresa, presupuesto/pedido generado desde la oportunidad, recordatorios por email.

## Roles y permisos dentro de la empresa

Matriz en `apps/api/app/security/erp_permissions.py` (única fuente de
verdad). Cada endpoint `/api/erp/...` declara su permiso
(`require("<módulo>:<acción>")`); un test recorre todas las rutas y falla si
aparece una nueva sin permiso. La interfaz recibe la lista en
`GET /api/erp/{id}/context` solo para mostrar/ocultar; el servidor revalida.

| Rol | Lee | Escribe |
|---|---|---|
| Administrador | todo | todo |
| Gerencia | todo | productos, terceros, ventas, compras, CRM |
| Finanzas | todo | terceros, facturas/cobros, facturas de proveedor/pagos, contabilidad, cierre de períodos |
| Contador | todo | contabilidad, cierre de períodos |
| Ventas | maestros, stock, ventas, cobranzas, CRM (no contabilidad, proveedores ni tablero) | terceros, pedidos, entregas, CRM |
| Compras | maestros, stock, compras, cuentas por pagar | productos, terceros, órdenes de compra, recepciones |
| Depósito | maestros, stock, pedidos y órdenes de compra | movimientos de stock, entregas, recepciones |
| Consulta / Auditor | todo | nada |

El personal de NEXATEC sigue sin acceso operacional implícito: para entrar
a los datos de una empresa hay que ser miembro con alguno de estos roles.
