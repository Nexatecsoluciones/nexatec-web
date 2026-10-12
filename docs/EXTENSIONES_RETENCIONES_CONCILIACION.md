# Retenciones y conciliación bancaria — arquitectura preparada (NO implementado)

Decisión del dueño (2026-10-12): diseñar los puntos de extensión ahora y
construir después, sin demorar la venta rápida ni la caja.

## Retenciones (IVA / Renta) — Paraguay

Antes de construir: confirmar con el contador las normas **vigentes** de la
DNIT (agentes de retención, porcentajes, mínimos, comprobante de
retención y su relación con SIFEN). Nada de esto se asume en el código.

Puntos de extensión ya existentes:
- **Pagos a proveedores** (`app/services/purchases.post_payment` /
  `_apply`): hoy el monto aplicado baja el saldo de la factura 1 a 1. Una
  retención será una **aplicación más** sobre la misma factura
  (`supplier_payment_allocations` + nueva tabla `withholdings` con tipo,
  base, tasa, monto, número de comprobante), de modo que pago + retención =
  saldo cancelado, sin tocar el motor de cuentas por pagar.
- **Asientos**: nueva clave de mapeo `WITHHOLDING_PAYABLE` (pasivo:
  retenciones a ingresar) usada por `accounting.post_supplier_payment`.
- **Cobros a clientes que retienen** (`receivables.post_receipt`): espejo,
  con `WITHHOLDING_RECEIVABLE` (activo: retenciones sufridas).
- **Comprobante**: impresión interna como hoy; la versión fiscal depende
  de SIFEN.

## Conciliación bancaria

Puntos de extensión ya existentes:
- Todo movimiento de dinero ya genera asiento contra `CASH` o `BANK`
  (cobros, pagos, caja ↔ banco: `cash_movements` `TO_BANK`/`FROM_BANK`).
  La conciliación trabaja sobre **líneas de mayor de la cuenta Bancos**,
  sin un segundo registro de movimientos.
- Diseño: `bank_accounts` (varias cuentas → subcuentas de 1.1.02),
  `bank_statements` + `bank_statement_lines` (importación CSV del extracto
  con vista previa), `bank_reconciliations` (línea de extracto ↔ línea de
  mayor, 1:N y N:1, con diferencias explicadas por asiento de ajuste:
  comisiones, intereses). Una línea de mayor conciliada no se puede
  revertir sin desconciliar (trigger).
- Tarjetas: hoy van a Bancos; con conciliación pasa a "Tarjetas a
  acreditar" hasta que el banco acredita (neto de comisión).

## Orden sugerido

1. Cuentas bancarias múltiples + importación de extracto + conciliación
   manual asistida (sugerencias por monto y fecha).
2. Retenciones en pagos a proveedores (después de validar normas).
3. Retenciones sufridas en cobros.
