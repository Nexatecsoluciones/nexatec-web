// Cliente del ERP. Igual que lib/api.ts: rutas relativas same-origin (el BFF
// reenvia a la API), cookie HttpOnly manejada por el navegador, nunca tokens
// en el cliente. Todo permiso se valida en el servidor; `can_write` del
// contexto solo decide que botones se muestran.

import { ApiError } from "@/lib/api";

export { ApiError };

export type Money = string;

export interface ErpContext {
  system_access_id: string;
  environment: "DEMO" | "PRODUCTION";
  expires_at: string | null;
  company_name: string | null;
  company_is_fictitious: boolean;
  member_role: string;
  role_label: string;
  permissions: string[];
  can_write: boolean;
  whatsapp_number: string;
}

export interface Page<T> {
  total: number;
  items: T[];
}

export interface Unit { id: string; code: string; name: string }
export interface Tax { code: string; name: string; rate: string }
export interface Branch { id: string; code: string; name: string; address: string | null; establishment_code: string | null; is_active: boolean }
export interface Warehouse { id: string; code: string; name: string; branch_id: string; is_active: boolean }
export interface Category { id: string; name: string; parent_id: string | null }

export interface Product {
  id: string; sku: string; name: string; description: string | null;
  product_type: "GOOD" | "SERVICE"; category_id: string | null; unit_id: string;
  tax_code: string; sale_price: Money; tracks_stock: boolean; is_active: boolean;
}

export interface Party {
  id: string; legal_name: string; trade_name: string | null; ruc: string | null; ruc_dv: string | null;
  ruc_status: "UNVERIFIED" | "FORMAT_OK" | "VERIFIED_PROVIDER" | "FICTITIOUS";
  is_customer: boolean; is_supplier: boolean; email: string | null; phone: string | null; address: string | null;
  payment_terms_days: number; credit_limit: Money; is_active: boolean;
}

export interface Balance {
  product_id: string; sku: string; product_name: string; warehouse_id: string; warehouse_code: string;
  on_hand: string; reserved: string; available: string; average_cost: string; stock_value: Money;
}

export interface Movement {
  id: string; movement_type: string; product_id: string; warehouse_id: string; quantity: string;
  direction: number; unit_cost: string; group_id: string | null; reference: string | null;
  reverses_movement_id: string | null; notes: string | null; created_at: string;
}

export interface OrderLine {
  line_no: number; product_id: string; description: string; quantity: string; unit_price: Money;
  discount_pct: string; tax_code: string; tax_rate: string; line_net: Money; line_tax: Money; line_total: Money;
  unit_cost: string | null;
  quantity_returned: string;
  amount_credited: Money;
}

export type OrderStatus = "DRAFT" | "CONFIRMED" | "DELIVERED" | "CANCELLED";

export interface SalesOrder {
  id: string; number: string; customer_id: string; warehouse_id: string; status: OrderStatus;
  payment_condition: "CASH" | "CREDIT"; currency: string; subtotal_net: Money; tax_total: Money; total: Money;
  notes: string | null; cancel_reason: string | null; created_at: string; lines: OrderLine[];
}

export interface SalesOrderSummary {
  id: string; number: string; customer_id: string; status: OrderStatus; payment_condition: "CASH" | "CREDIT";
  total: Money; created_at: string;
}

export interface CreditNote {
  id: string; number: string; invoice_id: string; kind: "RETURN" | "DISCOUNT"; issue_date: string; reason: string;
  restocked: boolean; total: Money; applied_amount: Money; unapplied_amount: Money; legal_notice: string | null;
}

export interface Invoice {
  id: string; number: string; order_id: string; customer_id: string; status: "ISSUED" | "VOIDED";
  fiscal_status: string; payment_condition: "CASH" | "CREDIT"; issue_date: string; due_date: string;
  taxable_10: Money; vat_10: Money; taxable_5: Money; vat_5: Money; exempt: Money; total: Money;
  balance_due: Money; void_reason: string | null; legal_notice: string | null;
}

export type PaymentMethod = "CASH" | "TRANSFER" | "CARD" | "CHECK" | "OTHER";

export interface Receipt {
  id: string; number: string; customer_id: string; receipt_date: string; method: PaymentMethod;
  amount: Money; unapplied_amount: Money; status: "POSTED" | "VOIDED"; reference: string | null;
  allocations: { invoice_id: string; amount: Money }[];
}

export interface AgingRow {
  current: Money; d1_30: Money; d31_60: Money; d61_90: Money; d90_plus: Money;
  total_due: Money; unapplied_advances: Money; net_balance: Money;
  customer_id?: string; customer_name?: string; supplier_id?: string; supplier_name?: string;
}

export interface PurchaseOrder {
  id: string; number: string; supplier_id: string; warehouse_id: string;
  status: "DRAFT" | "CONFIRMED" | "PARTIALLY_RECEIVED" | "RECEIVED" | "CLOSED" | "CANCELLED";
  total: Money; tax_total: Money; created_at: string;
  lines: { line_no: number; product_id: string; description: string; quantity: string; quantity_received: string;
           unit_price: Money; line_total: Money }[];
}

export interface SupplierInvoice {
  id: string; supplier_id: string; supplier_invoice_number: string; supplier_timbrado: string | null;
  purchase_order_id: string | null; status: "ISSUED" | "VOIDED"; issue_date: string; due_date: string;
  total: Money; balance_due: Money;
}

export interface SupplierPayment {
  id: string; number: string; supplier_id: string; payment_date: string; method: PaymentMethod;
  amount: Money; unapplied_amount: Money; status: "POSTED" | "VOIDED";
}

export interface Account {
  id: string; code: string; name: string; account_type: string; parent_id: string | null;
  is_postable: boolean; is_active: boolean;
}

export interface JournalEntry {
  id: string; number: string; entry_date: string; description: string; source_type: string;
  reverses_entry_id: string | null;
  lines: { line_no: number; account_id: string; debit: Money; credit: Money; description: string | null }[];
}

export interface Dashboard {
  date_from: string; date_to: string; sales_total: Money; sales_net: Money; invoice_count: number;
  average_ticket: Money; gross_margin: Money; gross_margin_pct: string; receivables_open: Money;
  receivables_overdue: Money; payables_open: Money; payables_due_7d: Money; cash_and_bank: Money;
  stock_value: Money; out_of_stock: number; pending_orders: { count: number; value: Money };
  top_products: { sku: string; name: string; quantity: string; net_sales: Money }[];
  sales_by_day: { date: string; sales_total: Money }[];
  definitions: Record<string, string>;
}

function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

async function call<T>(path: string, init?: RequestInit & { idempotent?: boolean }): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // Operaciones que mueven stock o plata: cada click genera una clave nueva;
  // si la red falla y el navegador reintenta la MISMA request, no duplica.
  if (init?.idempotent) headers["Idempotency-Key"] = newIdempotencyKey();
  const res = await fetch(path, { ...init, credentials: "include", headers: { ...headers, ...(init?.headers ?? {}) } });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      // sin JSON
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export function erp(accessId: string) {
  const base = `/api/erp/${accessId}`;
  const qs = (params?: Record<string, string | number | boolean | undefined | null>) => {
    if (!params) return "";
    const clean = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "");
    return clean.length ? `?${new URLSearchParams(clean.map(([k, v]) => [k, String(v)])).toString()}` : "";
  };
  return {
    get: <T>(path: string, params?: Record<string, string | number | boolean | undefined | null>) =>
      call<T>(`${base}${path}${qs(params)}`),
    post: <T>(path: string, body?: unknown, idempotent = false) =>
      call<T>(`${base}${path}`, { method: "POST", body: JSON.stringify(body ?? {}), idempotent }),
    put: <T>(path: string, body: unknown) => call<T>(`${base}${path}`, { method: "PUT", body: JSON.stringify(body) }),
    patch: <T>(path: string, body: unknown) => call<T>(`${base}${path}`, { method: "PATCH", body: JSON.stringify(body) }),
  };
}

const gs = new Intl.NumberFormat("es-PY", { maximumFractionDigits: 0 });
const qty = new Intl.NumberFormat("es-PY", { maximumFractionDigits: 4 });

export function money(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "-";
  return `Gs. ${gs.format(Number(v))}`;
}

export function quantity(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "-";
  return qty.format(Number(v));
}

export function day(iso: string | null | undefined): string {
  if (!iso) return "-";
  // Las fechas "YYYY-MM-DD" son fechas locales: no pasarlas por UTC.
  const d = /^\d{4}-\d{2}-\d{2}$/.test(iso) ? new Date(`${iso}T12:00:00`) : new Date(iso);
  return d.toLocaleDateString("es-PY", { timeZone: "America/Asuncion" });
}

export const STATUS_LABEL: Record<string, string> = {
  DRAFT: "Borrador", CONFIRMED: "Confirmado", DELIVERED: "Entregado", CANCELLED: "Cancelado",
  PARTIALLY_RECEIVED: "Recibido parcial", RECEIVED: "Recibido", CLOSED: "Cerrado",
  ISSUED: "Vigente", VOIDED: "Anulado", POSTED: "Registrado", RETURN: "Devolucion", DISCOUNT: "Bonificacion",
  CASH: "Contado", CREDIT: "Credito", TRANSFER: "Transferencia", CARD: "Tarjeta", CHECK: "Cheque", OTHER: "Otro",
  GOOD: "Bien", SERVICE: "Servicio",
  RECEIPT: "Entrada", ISSUE: "Salida", ADJUSTMENT_IN: "Ajuste +", ADJUSTMENT_OUT: "Ajuste -",
  TRANSFER_IN: "Transferencia entrada", TRANSFER_OUT: "Transferencia salida", REVERSAL: "Reversion",
  UNVERIFIED: "Sin verificar", FORMAT_OK: "Formato valido", VERIFIED_PROVIDER: "Verificado", FICTITIOUS: "Ficticio",
};

export const label = (code: string) => STATUS_LABEL[code] ?? code;
