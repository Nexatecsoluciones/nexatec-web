# Pagos (FASE 6)

> **Estado actual (2026-10-08): autoservicio de pago APAGADO.** Por decisión
> comercial, `POST /api/portal/checkout` responde `503` para tarjeta y para
> transferencia (`NEXATEC_SELF_CHECKOUT_CARD_ENABLED` y
> `NEXATEC_SELF_CHECKOUT_TRANSFER_ENABLED`, ambos `false` por defecto). La
> venta se cierra por WhatsApp y el alta/pago se registra a mano desde el
> Control Center. El código y las credenciales de Bancard se conservan.

Arquitectura modular: `app/services/payments/provider.py` define una
interfaz `PaymentProvider` para checkouts con tarjeta (hoy solo Bancard la
implementa). La transferencia bancaria es un flujo propio, manual, que
deliberadamente NO implementa esa interfaz -- forzar un review humano a la
misma forma que un checkout online no aporta nada.

## Bancard (vPOS) -- fuente de los nombres/algoritmo

**No se inventó ningún nombre de campo.** Toda la integración sigue el
documento oficial *"Integración con eCommerce Bancard — Compra Simple,
versión 0.3.1"* (mirror público de la Agencia Financiera de Desarrollo,
entidad estatal paraguaya, ya que Bancard no publica su documentación
técnica sin login en `comercios.bancard.com.py`), cruzado contra el SDK
oficial `Bancard/bancard-checkout-js` y el conector `Bancard/bancard-connectors`.
Donde el documento oficial no cubre algo (tokenización/cobro recurrente,
vPOS 2.0), se marca explícitamente como **no confirmado oficialmente**.

### Credenciales (nombres exactos)

| Variable | Uso |
|---|---|
| `BANCARD_ENV` | `staging` (`https://vpos.infonet.com.py:8888`) o `production` (`https://vpos.infonet.com.py`) |
| `BANCARD_PUBLIC_KEY` | Viaja en cada request, en claro |
| `BANCARD_PRIVATE_KEY` | **Nunca viaja en claro** -- solo se usa para calcular hashes MD5 server-side. Nunca loggear. |

No existe `commerce_id` ni API key por header: la autenticación es 100%
en el body de cada request (`public_key` + `token`).

**No hay sandbox self-service.** Se necesita una cuenta de comercio real
en `https://comercios.bancard.com.py` para obtener credenciales de
staging -- no hay credenciales de prueba públicas. Mientras
`BANCARD_PUBLIC_KEY`/`BANCARD_PRIVATE_KEY` esten vacíos, el endpoint de
checkout con tarjeta responde `503` explícito (`BancardNotConfiguredError`),
nunca un error confuso.

### Algoritmo de firma (MD5, orden exacto documentado)

Concatenación directa, sin separadores, hex lowercase. Los montos van
como string con dos decimales y punto (`"10330.00"`), **el mismo string**
en el JSON y en el hash.

| Operación | Fórmula |
|---|---|
| `single_buy` | `md5(private_key + shop_process_id + amount + currency)` |
| Confirmación/webhook | `md5(private_key + shop_process_id + "confirm" + amount + currency)` |
| `get_confirmation` (reconciliación) | `md5(private_key + shop_process_id + "get_confirmation")` |
| `rollback` | `md5(private_key + shop_process_id + "rollback" + "0.00")` (monto fijo `0.00`, no el monto real) |

Implementado y verificado con tests puros (sin red) en
`tests/test_bancard_provider.py`, calculando el MD5 esperado a mano con
`hashlib` contra los mismos valores.

### `shop_process_id` como clave de idempotencia

Bancard **no expone un `event_id` propio** (confirmado: la documentación
oficial no define ningún identificador de evento ni de reintento). La
clave de idempotencia es `shop_process_id`, generado por el comercio
(entero, único). `app/routers/payments.py::bancard_webhook` la usa así:
si la orden ya está en un estado terminal (`APPROVED`/`REJECTED`), el
webhook responde `200` sin reprocesar -- eso es toda la idempotencia que
hace falta, no una tabla de eventos separada.

### El webhook manda un ping de monitoreo vacío cada 5 minutos

Textual del documento oficial: *"Cada 5 minutos se realizará una petición
POST a la url de confirmación con un JSON vacío"*. El endpoint lo
detecta (`operation` ausente en el body) y responde `200` sin ningún
efecto secundario -- confirmado con test dedicado
(`test_bancard_webhook_empty_monitoring_ping_is_noop`).

### Validación de autenticidad del webhook

Bancard **no firma con HMAC ni manda header de firma, ni publica IPs
para allowlist**. La única autenticación es recalcular el token de
`confirm` con la `private_key` propia y compararlo -- como `amount` y
`currency` forman parte del hash, cualquier manipulación de esos campos
en tránsito invalida el token. Verificado con test que manda un `amount`
manipulado con un token calculado para el monto original: la orden
**nunca** queda aprobada (`test_bancard_webhook_rejects_tampered_amount`).

Bancard exige responder `200` dentro de 60 segundos o cierra la conexión
y marca la confirmación como inválida en su propia traza -- por eso el
webhook no hace trabajo pesado, solo valida y actualiza el estado.

### Reconciliación (pendiente de implementar, documentado)

El documento oficial recomienda, si a los 10 minutos de iniciado un
`single_buy` no llegó el webhook: llamar a `get_confirmation` y, si no
fue pagada, a `rollback`. Los tokens para ambas operaciones ya están
implementados (`token_get_confirmation`, `token_rollback`), pero el job
periódico que los dispare **no está implementado todavía** -- requiere
un scheduler de background jobs que no existe aún en el proyecto (ver
FASE de colas/workers pendiente). Sin esto, una orden de tarjeta iniciada
y nunca confirmada queda indefinidamente en `AWAITING_CARD_CONFIRMATION`.

### Moneda

Solo **PYG está confirmado** en la documentación oficial (`currency
String(3) - PYG (Gs)`, "Importe en Guaraníes"). USD aparece en SDKs de
terceros pero no en la fuente oficial; un manual técnico de terceros
indica explícitamente que el importe siempre va en guaraníes y que operar
en USD requeriría conversión a cotización del día. `Currency` (enum) hoy
solo define `PYG` a propósito -- agregar `USD` sin confirmarlo con
Bancard para esta cuenta específica seria inventar una capacidad que tal
vez no exista.

### Qué NO se implementa (a propósito)

- Captura de datos de tarjeta: **prohibido explícitamente** por Bancard
  ("La aplicación podrá registrar todos los datos del cliente... salvo
  todos aquellos que se relacionen a sus tarjetas de crédito"). El
  formulario de tarjeta lo sirve Bancard (iframe/redirect hospedado);
  este backend nunca ve PAN/CVV/vencimiento.
- Tokenización de tarjeta / cobro recurrente con alias: existe en vPOS
  2.0 pero solo está confirmada por SDKs de terceros, no por
  documentación oficial de Bancard -- no se implementó para no construir
  sobre una API no confirmada.
- Reconciliación automática (`get_confirmation`/`rollback` periódicos):
  preparado (tokens implementados), no agendado.

## Transferencia bancaria

Flujo completo e implementado (no depende de Bancard):

```
PENDING_TRANSFER -> PROOF_UPLOADED (implícito, ver nota) -> UNDER_REVIEW -> APPROVED | REJECTED
```

Nota: el modelo define `PROOF_UPLOADED` en el enum de estados, pero el
flujo real actual pasa directo de `PENDING_TRANSFER` a `UNDER_REVIEW` en
cuanto se sube el comprobante (`POST /api/portal/payment-orders/{id}/proof`),
sin un estado intermedio observable -- es lo mismo comprobante+revisión
en un solo paso, `PROOF_UPLOADED` queda reservado para cuando haga falta
distinguir "subido pero sin marcar para revisión todavía".

El comprobante se sube reusando el storage de FASE 5
(`MediaAsset`/Garage): nunca se guarda como base64 en la base de datos,
y un tenant no puede usar el comprobante de otro tenant para su propia
orden (verificado con test de aislamiento).

Aprobar una transferencia activa/extiende la suscripción del tenant
(`Subscription.status = ACTIVE`, `current_period_end` extendido según
`billing_period` del plan). El comprobante **no se considera prueba
definitiva por sí solo** -- requiere aprobación explícita de un
`ADMIN`/`SUPER_ADMIN`/`SUPPORT`/`BILLING` (nunca de un `CLIENT_*`, ni
siquiera sobre su propia orden -- verificado con test).

## Dinero

`NUMERIC(15,2)` en PostgreSQL (`sqlalchemy.Numeric`), nunca `float`, en
`Plan.price_amount` y `PaymentOrder.amount`. El monto de una orden se
calcula **siempre server-side** a partir del `Plan` en el momento de
crear la orden -- el endpoint de checkout no acepta ningún campo de
monto, solo `plan_id` (verificado con test explícito).

## Modelos

`Plan`, `Subscription` (estados `TRIAL`/`ACTIVE`/`PAST_DUE`/`SUSPENDED`/`CANCELLED`),
`PaymentOrder` (una fila por intento de pago, cubre ambos métodos).
`Invoice` como entidad separada **no se implementó** -- con el volumen
actual (sin facturación fiscal real todavía), `PaymentOrder` ya lleva
todo lo necesario para saber qué se cobró y cuándo; separar la
facturación es una mejora futura cuando haga falta emitir comprobantes
fiscales reales.

## Seguridad -- ver también docs/SECURITY.md

- El monto nunca viene del cliente.
- Ningún dato de tarjeta toca este servidor.
- El webhook fail-closed: sin `private_key` configurada,
  `verify_confirm_token` siempre devuelve `False`.
- IDOR: un tenant no puede ver ni actuar sobre las `payment_orders` de
  otro (mismo mecanismo de membership que el resto del sistema).
- RBAC: aprobar/rechazar transferencias es exclusivo de roles admin
  globales, nunca de `CLIENT_ADMIN`/`CLIENT_USER` ni siquiera sobre su
  propia orden.

## Fuentes consultadas

- *Integración con eCommerce Bancard — Compra Simple v0.3.1* (PDF oficial
  de Bancard, mirror publico de la AFD Paraguay)
- [Bancard/bancard-checkout-js](https://github.com/Bancard/bancard-checkout-js) (SDK oficial)
- [Bancard/bancard-connectors](https://github.com/Bancard/bancard-connectors) (oficial)
- [zrkb/bancard](https://github.com/zrkb/bancard) (SDK PHP de terceros, activo, usado solo para
  verificar el orden de concatenación del hash, no como fuente de nombres oficiales)
- Portal de Comercios Bancard (`comercios.bancard.com.py`) -- requiere
  cuenta de comercio, no se pudo acceder al contenido tecnico detras del login
