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
      detail = typeof body.detail === "string" ? body.detail
        : Array.isArray(body.detail)
          ? body.detail.map((d: { loc?: (string | number)[]; msg?: string }) =>
              `${(d.loc ?? []).filter((x) => x !== "body").join(" > ")}: ${d.msg ?? ""}`).join(" · ")
          : JSON.stringify(body.detail);
    } catch {
      // sin JSON
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type OpportunityStage = "NEW" | "QUALIFIED" | "PROPOSAL" | "NEGOTIATION" | "WON" | "LOST";
export const OPEN_STAGES: OpportunityStage[] = ["NEW", "QUALIFIED", "PROPOSAL", "NEGOTIATION"];
export type ActivityKind = "CALL" | "MEETING" | "EMAIL" | "WHATSAPP" | "TASK" | "NOTE";

export interface Lead {
  id: string; contact_name: string; company_name: string | null; email: string | null; phone: string | null;
  source: string | null; status: "OPEN" | "CONVERTED" | "DISCARDED"; party_id: string | null; notes: string | null;
  discard_reason: string | null; created_at: string;
}

export interface Opportunity {
  id: string; number: string; title: string; party_id: string | null; lead_id: string | null; account_name: string | null;
  stage: OpportunityStage; amount: Money; currency: string; probability: number; expected_close_date: string | null;
  notes: string | null; lost_reason: string | null; closed_at: string | null; created_at: string;
}

export interface Activity {
  id: string; kind: ActivityKind; subject: string; notes: string | null; party_id: string | null; lead_id: string | null;
  opportunity_id: string | null; due_at: string | null; done_at: string | null; created_at: string;
}

export interface Pipeline {
  stages: { stage: OpportunityStage; count: number; amount: Money; weighted: Money }[];
  open_count: number; open_amount: Money; forecast: Money; won_amount: Money; win_rate: number | null;
  overdue_activities: number;
}

// --- RR.HH. ---
export interface HrSettings {
  pay_period: "BIWEEKLY" | "MONTHLY"; workday_hours: string; rounding_minutes: number; overtime_multiplier: string;
  attendance_bonus_enabled: boolean; deduct_ips: boolean; ips_employee_pct: string; ips_employer_pct: string;
}
export interface HrCategory { id: string; code: string; default_trade: string | null; hourly_rate: Money | null; manual_rate: boolean }
export interface HrDaySchedule { weekday: number; start_time: string; end_time: string }
export interface HrSite {
  id: string; code: string; name: string; client_name: string | null; location: string | null; start_time: string;
  end_time: string; workdays: number[]; tolerance_minutes: number; latitude: string | null; longitude: string | null;
  geofence_radius_m: number; day_schedules: HrDaySchedule[]; is_active: boolean;
}
export interface HrEmployee {
  id: string; national_id: string; last_names: string; first_names: string; full_name: string; trade: string | null;
  category_id: string | null; phone: string | null; email: string | null; pay_method: "CASH" | "TRANSFER";
  bank_account: string | null; hourly_rate: Money | null; bonus_per_hour: Money | null; ips_entry: string | null;
  ips_exit: string | null; ips_notes: string | null; address: string | null; neighborhood: string | null; city: string | null;
  birth_date: string | null; family_notes: string | null; training: string | null; skills: string | null;
  references_notes: string | null; custom_start: string | null; custom_end: string | null; tracks_attendance: boolean;
  is_active: boolean; current_site_id: string | null;
}
export interface HrAttendance {
  id: string; employee_id: string; site_id: string; work_date: string; time_in: string | null; time_out: string | null;
  regular_hours: string; overtime_hours: string; overtime_status: "NONE" | "PENDING" | "APPROVED" | "REJECTED";
  paid_hours: string; manual_override: boolean; source: string; notes: string | null; employee_name: string | null;
}
export interface HrAdvance {
  id: string; number: string; employee_id: string; employee_name: string | null; site_id: string | null; advance_date: string;
  amount: Money; pay_method: string; notes: string | null; voided: boolean; void_reason: string | null;
}
export interface HrAbsence { id: string; employee_id: string; start_date: string; end_date: string; kind: string; notes: string | null }
export interface HrPayrollLine {
  employee_id: string; employee_name: string; national_id: string; pay_method: string; bank_account: string | null;
  hourly_rate: Money; regular_hours: string; overtime_hours: string; days_worked: number; unexcused_absences: number;
  base_amount: Money; overtime_amount: Money; bonus_amount: Money; adjustment: Money; gross: Money; ips_employee: Money;
  ips_employer: Money; advances: Money; net: Money; bonus_forced: boolean; adjustment_note: string | null;
}
export interface HrPayroll {
  id: string; number: string; period_start: string; period_end: string; site_id: string | null; status: "DRAFT" | "CLOSED";
  total_hours: string; total_gross: Money; total_ips_employee: Money; total_ips_employer: Money; total_advances: Money;
  total_net: Money; closed_at: string | null; lines: HrPayrollLine[];
}
export interface HrDevice { id: string; site_id: string; name: string; is_active: boolean; bound: boolean; last_used_at: string | null; token: string | null }
export const WEEKDAYS = ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"];
export const hhmm = (t: string | null | undefined) => (t ? t.slice(0, 5) : "-");

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
    del: <T>(path: string) => call<T>(`${base}${path}`, { method: "DELETE" }),
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
  NEW: "Nuevo", QUALIFIED: "Calificado", PROPOSAL: "Propuesta", NEGOTIATION: "Negociacion", WON: "Ganada", LOST: "Perdida",
  OPEN: "Abierto", CONVERTED: "Convertido", DISCARDED: "Descartado",
  BIWEEKLY: "Quincenal", MONTHLY: "Mensual", PENDING: "Pendiente", APPROVED: "Aprobada", REJECTED: "Rechazada",
  VACATION: "Vacaciones", PERMISSION: "Permiso", MEDICAL: "Reposo medico", MANUAL: "Manual", DEVICE: "Celular", IMPORT: "Importada",
  CALL: "Llamada", MEETING: "Reunion", EMAIL: "Email", WHATSAPP: "WhatsApp", TASK: "Tarea", NOTE: "Nota",
};

export const label = (code: string) => STATUS_LABEL[code] ?? code;
