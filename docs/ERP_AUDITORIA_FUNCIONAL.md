# Auditoría funcional del ERP — Etapa 0 (2026-10-12)

Respuesta al "Prompt maestro" de auditoría E2E. Este documento separa lo
**observado** (código, tests y bases reales) de lo **propuesto**. Nada de lo
que figura como PASS se basa en mocks: son tests contra PostgreSQL real
(una base por empresa) o navegador real (Playwright/Chromium).

## Correcciones a premisas del prompt

- `staging.nexatecpy.com` **ya no existe** (se eliminó del DNS y del túnel
  el 2026-10-08 a pedido del dueño). Hay **un solo entorno**:
  `https://nexatecpy.com`, `NEXATEC_ENV=production`. El sandbox del agente es
  una base aislada **dentro de ese mismo entorno** (no un staging aparte).
  Consecuencia: cualquier publicación de código es publicación a producción.
- Las unidades systemd y el archivo de entorno conservan el nombre
  `*-staging` por historia; no indican un entorno distinto.

## Prioridad cero — sandbox del agente (RESUELTO en código; falta publicar)

Observado (consultas de solo lectura a `nexatec_control`):

| Recurso | Estado |
|---|---|
| Tenant `sandbox-agente-13695388` | creado 2026-10-12 22:57 UTC |
| Acceso `59577a2c-…` | DEMO, ACTIVE, vence 2027-10-12 |
| Base `nxt_f1ff5b2e_b7941317_demo` | READY, en la última migración, empresa ficticia, 12 productos, 5 facturas, 3 prospectos, 8 empleados, 1 planilla cerrada |
| Usuario de servicio + membresía | 1, Administrador |
| Llave `nxk_UqtWk2om…` | emitida, **nunca mostrada ni usada** |

Causa raíz: `create_sandbox` devolvía el objeto ORM `SystemAccess`; el CLI
cerraba la sesión y luego leía `access.id` → `DetachedInstanceError` (los
objetos se expiran en cada commit). Todo había quedado commiteado; solo
falló la impresión, por eso la llave nunca se mostró.

Corrección: el servicio devuelve los ids como valores simples capturados
antes del commit; el CLI ya no toca objetos ORM fuera de la sesión. Además
es idempotente por nombre: si el sandbox existe, no crea otro e indica cómo
emitir una llave nueva (salida sin secretos). Test de regresión que corre el
comando real y verifica que una segunda corrida no duplica.

Acción pendiente (persona en el servidor, después de publicar): revocar la
llave nunca mostrada y emitir una nueva (ver "Decisiones").

OpenAPI del agente: confirmado que `authorization` aparecía como parámetro
opcional y que se publicaban 232 esquemas. Ahora: `HTTPBearer` declarado en
`securitySchemes` + `security` global, sin header/cookie sueltos, y solo los
esquemas alcanzables (3 en la universal; ~34 en `?module=ventas`).

Aislamiento (tests contra base real): llave en otro acceso → 404; atada a
acceso PRODUCTION → 403; Control Center / portal / cuenta → 401; revocada o
vencida → 401; vencimiento 90 días; límite 120/min → 429; acciones auditadas
a nombre del usuario de servicio; reinicio solo por CLI y solo
`sandbox-agente-*`. **Tests: 283 pasan** (antes 281).

## Inventario real del ERP

Leyenda: ✅ existe y tiene tests de integración · 🟡 parcial · ❌ no existe.

| Área (prompt) | Estado | Observado |
|---|---|---|
| A. Login, MFA, recuperación, roles | ✅ | Sesiones por etapas, MFA TOTP con QR, Turnstile real, reset por Brevo, 13 roles de empresa con matriz de permisos. Cambio de empresa: un usuario por acceso (portal). Sin selección de sucursal por usuario. |
| B. Clientes | 🟡 | RUC + DV (módulo 11), límite de crédito, plazo, búsqueda, estado de cuenta, antigüedad. **Falta**: varias direcciones/sucursales del cliente, exportación, historial unificado en una pantalla. Sin consulta DNIT (no hay servicio oficial autorizado integrado). |
| C. Proveedores | 🟡 | Mismo maestro de terceros; OC, facturas de proveedor, pagos, anticipos, antigüedad. **Falta**: retenciones, contactos múltiples. |
| D. Productos | 🟡 | SKU, nombre, categoría, unidad, IVA, precio IVA incl., costo promedio, activo. **Falta**: códigos de barras, marca, variantes, listas de precios, promociones, stock mínimo, proveedor habitual. |
| E. Compras | 🟡 | OC → confirmación → recepción parcial → factura de proveedor (con control de IVA) → pago → asiento. **Falta**: requisición, adjunto de la factura, NC de compra/devolución a proveedor. |
| F. Ventas | 🟡 | Pedido con reserva de stock, contado/crédito con límite, entrega, factura interna "sin validez tributaria", cobro, NC (devolución y bonificación). **Falta**: cotización, **POS/venta rápida**, escáner, pagos mixtos, vuelto. |
| G. Inventario | 🟡 | Varios depósitos, kardex inmutable (trigger), costo promedio, transferencias, ajustes, reversas, no negativo con bloqueo concurrente (test de 10 hilos). **Falta**: conteos físicos, lote/serie/vencimiento, alertas de mínimo. |
| H. Caja y bancos | ❌ | No hay turnos de caja, arqueo ni conciliación bancaria. Los cobros/pagos usan cuentas Caja/Bancos del plan contable. |
| I. Cuentas por cobrar | ✅ | Aging 0/1-30/31-60/61-90/90+, anticipos, imputación, cobros parciales, estado de cuenta. Falta corte por vendedor/sucursal. |
| J. Cuentas por pagar | ✅ | Igual que AR para proveedores. Falta bloqueo explícito de timbrado+número duplicado por proveedor (verificar) y aprobaciones. |
| K. Contabilidad | ✅ | 25 cuentas, mapeos, asientos automáticos por documento, partida doble (trigger diferido), mayor, sumas y saldos, resultados, balance, cierre/reapertura de período. |
| L. CRM | 🟡 | Prospecto → oportunidad → ganada/perdida, actividades, embudo. **Falta**: cotización/pedido desde la oportunidad. |
| M. RR.HH. | ✅ | Obras, personal, asistencia (manual y celular con geocerca), extras con aprobación, adelantos, ausencias, planillas con IPS, recibos, CSV. **Sin biometría** (decisión explícita). |
| N. Tablero y reportes | 🟡 | Tablero con definiciones de cada KPI. **Falta**: tableros por rol, exportación CSV/XLSX/PDF general, libro de ventas/compras, utilidad por producto, rotación. |
| O. Seguridad | ✅ | Aislamiento por base, permisos en servidor, auditoría, idempotencia en operaciones de dinero/stock, Turnstile, rate limit en lo público. Falta request-id/correlación en logs. |
| Importación CSV/XLSX | ❌ | No existe. |
| Adjuntos (facturas de proveedor) | ❌ | Garage existe en la plataforma, no conectado al ERP. |

## Evidencia E2E existente (navegador real, 2026-10-08)

`erp.e2e.mjs` 19/19 sobre producción con tenant descartable: venta completa,
cobro, NC, impresión con marca de agua y PDF, balance cuadrado, CRM, RR.HH.
(planilla cerrada + recibos + marcación desde celular), móvil sin desborde.
Matriz por rol (gerente, cajero, comprador, depósito, tesorero, contador,
auditor) a través de la interfaz: **NO ejecutada todavía** — los permisos por
rol están probados por API (`test_erp_permissions.py`), no por interfaz.

## Hallazgos priorizados

| P | Hallazgo | Impacto | Propuesta |
|---|---|---|---|
| P0 | CLI del sandbox falla y pierde la llave | ChatGPT no puede conectarse | **Corregido** (falta publicar + rotar llave) |
| P0 | OpenAPI del agente con auth como parámetro y 232 esquemas | Importación confusa en GPT | **Corregido** (falta publicar) |
| P1 | No hay caja (turnos, arqueo) | Comercios con mostrador no pueden operar el día | Etapa 2: caja por usuario/turno integrada a cobros y GL |
| P1 | No hay venta rápida (POS) ni códigos de barras | Vender toma 4 pantallas/acciones | Etapa 2: POS sobre el MISMO motor de ventas (pedido+entrega+factura+cobro en una transacción), escáner HID, pagos mixtos |
| P1 | Sin importación CSV | Alta inicial lenta | Etapa 3: importación con vista previa y confirmación |
| P1 | Menú por objetos (CRUD), no por tareas | Usuario no técnico se pierde | Etapa 2: menú por tareas y tablero por rol |
| P2 | Listas de precios, promociones, mínimos | Comercios medianos | Etapa 3 |
| P2 | Exportaciones y reportes de libro | Contador | Etapa 5 |
| P2 | Adjuntos de comprobantes | Respaldo documental | Etapa 3 (Garage, URLs firmadas) |

## Decisiones que requieren aprobación

1. **Publicar las correcciones P0** (reinicio de la API en producción, ~5 s
   de corte; HESED no se toca). Luego, en el servidor:
   `revoke-agent-key nxk_UqtWk2om` y `agent-key 59577a2c-c14c-4dd9-8f46-0527efbf052d`.
2. **Alcance de la Etapa 2 (POS + caja + códigos de barras)**: cambia el
   modelo de datos (tablas nuevas de caja, turnos y códigos) y agrega un
   flujo nuevo de venta. Propuesta en la sección siguiente.
3. Retenciones, conciliación bancaria y adjuntos: confirmar si son
   necesarios para los primeros clientes antes de construirlos.

## Propuesta Etapa 2 (para aprobar)

- `cash_registers` (cajas por sucursal), `cash_sessions` (apertura con fondo,
  cierre con arqueo y diferencia, aprobación), `cash_movements` (ingresos y
  egresos, inmutables). Cobros en efectivo de una caja exigen turno abierto.
- `product_barcodes` (varios por producto, uno principal, únicos por
  empresa; EAN-13/UPC-A validados por dígito verificador; código interno
  Code 128 con prefijo propio, nunca simula un GTIN de GS1). Etiquetas A4 y
  térmicas en PDF.
- Pantalla **Vender** (POS): foco permanente en el buscador, lector HID con
  Enter, carrito, cliente ocasional o identificado, pagos mixtos con vuelto,
  confirmación única idempotente → reutiliza `sales`/`receivables`/`accounting`
  (un solo motor). Comprobante interno con "SIN VALIDEZ TRIBUTARIA".
- Tests de integración + E2E: escaneo simulado → venta de 2 unidades →
  cobro mixto → cierre de caja → kardex/costo → AR/GL conciliados; doble
  clic = una sola venta; dos ventas simultáneas del último producto.
