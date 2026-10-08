"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, type AgingRow, type Page, type Party, type PaymentMethod, type PurchaseOrder, type SupplierInvoice, type SupplierPayment } from "@/lib/erp";

const today = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Asuncion" });
const EMPTY_INV = { supplier_id: "", supplier_invoice_number: "", supplier_timbrado: "", issue_date: today(), purchase_order_id: "",
  taxable_10: "", vat_10: "", taxable_5: "", vat_5: "", exempt: "" };

export default function PagosPage() {
  const { client, ctx } = useErp();
  const [inv, setInv] = useState(EMPTY_INV);
  const [pay, setPay] = useState({ supplier_id: "", method: "TRANSFER" as PaymentMethod, amount: "", invoice_id: "", reference: "" });
  const action = useAction();

  const suppliers = useLoad(async () => (await client.get<Page<Party>>("/parties", { role: "supplier", limit: 200 })).items, []);
  const invoices = useLoad(() => client.get<Page<SupplierInvoice>>("/supplier-invoices", { open_only: true, limit: 100 }), []);
  const payments = useLoad(() => client.get<Page<SupplierPayment>>("/supplier-payments", { limit: 10 }), []);
  const aging = useLoad(() => client.get<AgingRow[]>("/payables/aging"), []);
  const pos = useLoad(
    () => (inv.supplier_id ? client.get<Page<PurchaseOrder>>("/purchases/orders", { supplier_id: inv.supplier_id, limit: 100 }) : Promise.resolve(null)),
    [inv.supplier_id],
  );

  const name = (id: string) => suppliers.data?.find((s) => s.id === id)?.legal_name ?? "-";
  const reloadAll = () => { invoices.reload(); payments.reload(); aging.reload(); };
  const n = (v: string) => (v === "" ? "0" : v);

  // Sugerencia de IVA a partir de lo gravado (el usuario puede corregirlo a lo
  // que diga la factura; el servidor valida +-1 Gs.).
  const suggestVat = (taxable: string, rate: number) => (taxable ? String(Math.round((Number(taxable) * rate) / 100)) : "");
  const total = ["taxable_10", "vat_10", "taxable_5", "vat_5", "exempt"].reduce((a, k) => a + Number(inv[k as keyof typeof inv] || 0), 0);

  async function register(e: React.FormEvent) {
    e.preventDefault();
    const ok = await action.run(() => client.post("/supplier-invoices", {
      ...inv, supplier_timbrado: inv.supplier_timbrado || null, purchase_order_id: inv.purchase_order_id || null,
      taxable_10: n(inv.taxable_10), vat_10: n(inv.vat_10), taxable_5: n(inv.taxable_5), vat_5: n(inv.vat_5), exempt: n(inv.exempt),
    }), "Factura de proveedor registrada.");
    if (ok) {
      setInv({ ...EMPTY_INV, supplier_id: inv.supplier_id });
      reloadAll();
    }
  }

  async function postPayment(e: React.FormEvent) {
    e.preventDefault();
    const allocations = pay.invoice_id ? [{ invoice_id: pay.invoice_id, amount: pay.amount }] : [];
    if (await action.run(() => client.post("/supplier-payments", { supplier_id: pay.supplier_id, method: pay.method,
      amount: pay.amount, allocations, reference: pay.reference || null }, true), "Pago registrado.")) {
      setPay({ ...pay, amount: "", invoice_id: "", reference: "" });
      reloadAll();
    }
  }

  async function voidPayment(p: SupplierPayment) {
    const reason = window.prompt(`Motivo para anular el pago ${p.number}:`);
    if (reason && (await action.run(() => client.post(`/supplier-payments/${p.id}/void`, { reason }), "Pago anulado."))) reloadAll();
  }

  const openForSupplier = (invoices.data?.items ?? []).filter((i) => i.supplier_id === pay.supplier_id);

  return (
    <>
      <PageTitle title="Proveedores y pagos" subtitle="Se registran las facturas que emite el proveedor (son documentos de terceros)" />
      {(suppliers.error || invoices.error || payments.error || aging.error) && <Notice>{suppliers.error ?? invoices.error ?? payments.error ?? aging.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {ctx.can_write && (
        <div className="mb-5 grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Panel title="Registrar factura de proveedor">
            <form onSubmit={register} className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Proveedor">
                <Select required value={inv.supplier_id} onChange={(e) => setInv({ ...inv, supplier_id: e.target.value, purchase_order_id: "" })}>
                  <option value="">Elegir...</option>
                  {(suppliers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.legal_name}</option>)}
                </Select>
              </Field>
              <Field label="Orden de compra (opcional)">
                <Select value={inv.purchase_order_id} onChange={(e) => setInv({ ...inv, purchase_order_id: e.target.value })}>
                  <option value="">Sin OC (gasto)</option>
                  {(pos.data?.items ?? []).filter((p) => !["DRAFT", "CANCELLED"].includes(p.status)).map((p) => <option key={p.id} value={p.id}>{p.number} · {label(p.status)}</option>)}
                </Select>
              </Field>
              <Field label="Numero (ej. 001-001-0000123)"><Input required pattern="[0-9A-Za-z\-/]+" value={inv.supplier_invoice_number} onChange={(e) => setInv({ ...inv, supplier_invoice_number: e.target.value })} /></Field>
              <Field label="Timbrado"><Input inputMode="numeric" pattern="[0-9]*" value={inv.supplier_timbrado} onChange={(e) => setInv({ ...inv, supplier_timbrado: e.target.value })} /></Field>
              <Field label="Fecha de emision"><Input type="date" required value={inv.issue_date} onChange={(e) => setInv({ ...inv, issue_date: e.target.value })} /></Field>
              <div />
              <Field label="Gravado 10%"><Input type="number" min="0" value={inv.taxable_10} onChange={(e) => setInv({ ...inv, taxable_10: e.target.value, vat_10: suggestVat(e.target.value, 10) })} /></Field>
              <Field label="IVA 10%"><Input type="number" min="0" value={inv.vat_10} onChange={(e) => setInv({ ...inv, vat_10: e.target.value })} /></Field>
              <Field label="Gravado 5%"><Input type="number" min="0" value={inv.taxable_5} onChange={(e) => setInv({ ...inv, taxable_5: e.target.value, vat_5: suggestVat(e.target.value, 5) })} /></Field>
              <Field label="IVA 5%"><Input type="number" min="0" value={inv.vat_5} onChange={(e) => setInv({ ...inv, vat_5: e.target.value })} /></Field>
              <Field label="Exento"><Input type="number" min="0" value={inv.exempt} onChange={(e) => setInv({ ...inv, exempt: e.target.value })} /></Field>
              <div className="flex items-end justify-between gap-2">
                <span className="text-sm">Total: <strong>{money(total)}</strong></span>
                <SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar</SmallButton>
              </div>
            </form>
          </Panel>

          <Panel title="Registrar pago">
            <form onSubmit={postPayment} className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Proveedor">
                <Select required value={pay.supplier_id} onChange={(e) => setPay({ ...pay, supplier_id: e.target.value, invoice_id: "" })}>
                  <option value="">Elegir...</option>
                  {(suppliers.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.legal_name}</option>)}
                </Select>
              </Field>
              <Field label="Aplicar a factura">
                <Select value={pay.invoice_id} onChange={(e) => {
                  const i = openForSupplier.find((x) => x.id === e.target.value);
                  setPay({ ...pay, invoice_id: e.target.value, amount: i ? String(Number(i.balance_due)) : pay.amount });
                }}>
                  <option value="">Sin aplicar (anticipo)</option>
                  {openForSupplier.map((i) => <option key={i.id} value={i.id}>{i.supplier_invoice_number} · saldo {money(i.balance_due)}</option>)}
                </Select>
              </Field>
              <Field label="Medio">
                <Select value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value as PaymentMethod })}>
                  {["TRANSFER", "CASH", "CHECK", "CARD", "OTHER"].map((m) => <option key={m} value={m}>{label(m)}</option>)}
                </Select>
              </Field>
              <Field label="Monto (Gs.)"><Input type="number" min="1" required value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
              <Field label="Referencia"><Input value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field>
              <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar pago</SmallButton></div>
            </form>
          </Panel>
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Panel title="Facturas de proveedor pendientes">
          <Table
            rows={invoices.data?.items ?? []}
            rowKey={(i) => i.id}
            empty="No hay facturas pendientes."
            columns={[
              { header: "Proveedor", cell: (i) => name(i.supplier_id) },
              { header: "Numero", cell: (i) => i.supplier_invoice_number },
              { header: "Vence", cell: (i) => day(i.due_date) },
              { header: "Total", cell: (i) => money(i.total), align: "right" },
              { header: "Saldo", cell: (i) => money(i.balance_due), align: "right" },
            ]}
          />
        </Panel>
        <Panel title="Antiguedad de cuentas por pagar">
          <Table
            rows={aging.data ?? []}
            rowKey={(r) => r.supplier_id ?? ""}
            empty="No hay saldos pendientes."
            columns={[
              { header: "Proveedor", cell: (r) => r.supplier_name },
              { header: "Al dia", cell: (r) => money(r.current), align: "right" },
              { header: "1-30", cell: (r) => money(r.d1_30), align: "right" },
              { header: "31-60", cell: (r) => money(r.d31_60), align: "right" },
              { header: "+60", cell: (r) => money(Number(r.d61_90) + Number(r.d90_plus)), align: "right" },
              { header: "Neto", cell: (r) => money(r.net_balance), align: "right" },
            ]}
          />
        </Panel>
        <Panel title="Ultimos pagos" className="xl:col-span-2">
          <Table
            rows={payments.data?.items ?? []}
            rowKey={(p) => p.id}
            columns={[
              { header: "Numero", cell: (p) => p.number },
              { header: "Fecha", cell: (p) => day(p.payment_date) },
              { header: "Proveedor", cell: (p) => name(p.supplier_id) },
              { header: "Medio", cell: (p) => label(p.method) },
              { header: "Monto", cell: (p) => money(p.amount), align: "right" },
              { header: "Sin aplicar", cell: (p) => money(p.unapplied_amount), align: "right" },
              { header: "Estado", cell: (p) => label(p.status) },
              ...(ctx.can_write ? [{ header: "", cell: (p: SupplierPayment) => p.status === "POSTED"
                ? <SmallButton tone="danger" disabled={action.busy} onClick={() => voidPayment(p)}>Anular</SmallButton> : null }] : []),
            ]}
          />
        </Panel>
      </div>
    </>
  );
}
