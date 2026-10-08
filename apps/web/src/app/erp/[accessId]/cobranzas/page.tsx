"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, type AgingRow, type Invoice, type Page, type Party, type PaymentMethod, type Receipt } from "@/lib/erp";

const LIMIT = 25;

export default function CobranzasPage() {
  const { client, can } = useErp();
  const [openOnly, setOpenOnly] = useState(true);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Invoice | null>(null);
  const [pay, setPay] = useState({ customer_id: "", method: "CASH" as PaymentMethod, amount: "", invoice_id: "", reference: "" });
  const action = useAction();

  const customers = useLoad(async () => (await client.get<Page<Party>>("/parties", { role: "customer", limit: 200 })).items, []);
  const invoices = useLoad(() => client.get<Page<Invoice>>("/invoices", { open_only: openOnly, limit: LIMIT, offset }), [openOnly, offset]);
  const receipts = useLoad(() => client.get<Page<Receipt>>("/receipts", { limit: 10 }), []);
  const aging = useLoad(() => client.get<AgingRow[]>("/receivables/aging"), []);
  const openForCustomer = useLoad(
    () => (pay.customer_id ? client.get<Page<Invoice>>("/invoices", { customer_id: pay.customer_id, open_only: true, limit: 200 }) : Promise.resolve(null)),
    [pay.customer_id],
  );

  const name = (id: string) => customers.data?.find((c) => c.id === id)?.legal_name ?? "-";

  function reloadAll() {
    invoices.reload();
    receipts.reload();
    aging.reload();
    openForCustomer.reload();
  }

  async function postReceipt(e: React.FormEvent) {
    e.preventDefault();
    const allocations = pay.invoice_id ? [{ invoice_id: pay.invoice_id, amount: pay.amount }] : [];
    const ok = await action.run(() => client.post("/receipts", {
      customer_id: pay.customer_id, method: pay.method, amount: pay.amount, allocations, reference: pay.reference || null,
    }, true), allocations.length ? "Cobro registrado y aplicado." : "Cobro registrado como anticipo.");
    if (ok) {
      setPay({ ...pay, amount: "", invoice_id: "", reference: "" });
      reloadAll();
    }
  }

  async function voidReceipt(r: Receipt) {
    const reason = window.prompt(`Motivo para anular el cobro ${r.number}:`);
    if (reason && (await action.run(() => client.post(`/receipts/${r.id}/void`, { reason }), "Cobro anulado: saldos restituidos."))) reloadAll();
  }

  async function voidInvoice(inv: Invoice) {
    const reason = window.prompt(`Motivo para anular la factura ${inv.number}:`);
    if (reason && (await action.run(() => client.post(`/invoices/${inv.id}/void`, { reason }), "Factura anulada."))) {
      setSelected(null);
      reloadAll();
    }
  }

  return (
    <>
      <PageTitle title="Facturas y cobros" subtitle="Comprobantes internos de gestion: no son facturas tributarias" />
      <Notice kind="info">Las facturas de este sistema son <strong>documentos de simulacion, sin validez tributaria</strong>. La emision electronica (SIFEN) no esta habilitada.</Notice>
      {(invoices.error || receipts.error || aging.error || customers.error) && <Notice>{invoices.error ?? receipts.error ?? aging.error ?? customers.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {can("receivables:write") && (
        <Panel title="Registrar cobro" className="mb-5">
          <form onSubmit={postReceipt} className="grid grid-cols-1 gap-3 md:grid-cols-6">
            <Field label="Cliente">
              <Select required value={pay.customer_id} onChange={(e) => setPay({ ...pay, customer_id: e.target.value, invoice_id: "" })}>
                <option value="">Elegir...</option>
                {(customers.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
              </Select>
            </Field>
            <Field label="Aplicar a factura">
              <Select value={pay.invoice_id} onChange={(e) => {
                const inv = openForCustomer.data?.items.find((i) => i.id === e.target.value);
                setPay({ ...pay, invoice_id: e.target.value, amount: inv ? String(Number(inv.balance_due)) : pay.amount });
              }}>
                <option value="">Sin aplicar (anticipo)</option>
                {(openForCustomer.data?.items ?? []).map((i) => <option key={i.id} value={i.id}>{i.number} · saldo {money(i.balance_due)}</option>)}
              </Select>
            </Field>
            <Field label="Medio">
              <Select value={pay.method} onChange={(e) => setPay({ ...pay, method: e.target.value as PaymentMethod })}>
                {["CASH", "TRANSFER", "CARD", "CHECK", "OTHER"].map((m) => <option key={m} value={m}>{label(m)}</option>)}
              </Select>
            </Field>
            <Field label="Monto (Gs.)"><Input type="number" min="1" step="1" required value={pay.amount} onChange={(e) => setPay({ ...pay, amount: e.target.value })} /></Field>
            <Field label="Referencia"><Input value={pay.reference} onChange={(e) => setPay({ ...pay, reference: e.target.value })} /></Field>
            <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar</SmallButton></div>
          </form>
        </Panel>
      )}

      <div className="mb-5 grid grid-cols-1 gap-5 xl:grid-cols-[1.3fr_1fr]">
        <Panel title="Facturas">
          <label className="mb-3 flex items-center gap-2 text-sm">
            <input type="checkbox" checked={openOnly} onChange={(e) => { setOffset(0); setOpenOnly(e.target.checked); }} /> Solo con saldo pendiente
          </label>
          <Table
            rows={invoices.data?.items ?? []}
            rowKey={(i) => i.id}
            columns={[
              { header: "Numero", cell: (i) => <button className="text-nx-accent hover:underline" onClick={() => setSelected(i)}>{i.number}</button> },
              { header: "Cliente", cell: (i) => name(i.customer_id) },
              { header: "Emision", cell: (i) => day(i.issue_date) },
              { header: "Vence", cell: (i) => day(i.due_date) },
              { header: "Total", cell: (i) => money(i.total), align: "right" },
              { header: "Saldo", cell: (i) => money(i.balance_due), align: "right" },
              { header: "Estado", cell: (i) => label(i.status) },
            ]}
          />
          <Pager total={invoices.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
        </Panel>

        <Panel title={selected ? `Factura ${selected.number}` : "Detalle de factura"}>
          {!selected ? <p className="text-sm text-nx-muted">Elegir una factura.</p> : (
            <div className="flex flex-col gap-2 text-sm">
              <p className="rounded-lg border border-amber-300/40 bg-amber-400/10 p-2 text-center font-bold text-amber-100">{selected.legal_notice}</p>
              <p>{name(selected.customer_id)} · {label(selected.payment_condition)}</p>
              <dl className="grid grid-cols-2 gap-1 tabular-nums">
                <dt className="text-nx-muted">Gravado 10%</dt><dd className="text-right">{money(selected.taxable_10)}</dd>
                <dt className="text-nx-muted">IVA 10%</dt><dd className="text-right">{money(selected.vat_10)}</dd>
                <dt className="text-nx-muted">Gravado 5%</dt><dd className="text-right">{money(selected.taxable_5)}</dd>
                <dt className="text-nx-muted">IVA 5%</dt><dd className="text-right">{money(selected.vat_5)}</dd>
                <dt className="text-nx-muted">Exento</dt><dd className="text-right">{money(selected.exempt)}</dd>
                <dt className="font-bold">Total</dt><dd className="text-right font-bold">{money(selected.total)}</dd>
                <dt className="text-nx-muted">Saldo</dt><dd className="text-right">{money(selected.balance_due)}</dd>
              </dl>
              {can("receivables:write") && selected.status === "ISSUED" && (
                <div className="text-right"><SmallButton tone="danger" disabled={action.busy} onClick={() => voidInvoice(selected)}>Anular factura</SmallButton></div>
              )}
            </div>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Panel title="Antiguedad de saldos">
          <Table
            rows={aging.data ?? []}
            rowKey={(r) => r.customer_id ?? ""}
            empty="No hay saldos pendientes."
            columns={[
              { header: "Cliente", cell: (r) => r.customer_name },
              { header: "Al dia", cell: (r) => money(r.current), align: "right" },
              { header: "1-30", cell: (r) => money(r.d1_30), align: "right" },
              { header: "31-60", cell: (r) => money(r.d31_60), align: "right" },
              { header: "61-90", cell: (r) => money(r.d61_90), align: "right" },
              { header: "+90", cell: (r) => money(r.d90_plus), align: "right" },
              { header: "Anticipos", cell: (r) => money(r.unapplied_advances), align: "right" },
              { header: "Neto", cell: (r) => money(r.net_balance), align: "right" },
            ]}
          />
        </Panel>
        <Panel title="Ultimos cobros">
          <Table
            rows={receipts.data?.items ?? []}
            rowKey={(r) => r.id}
            columns={[
              { header: "Numero", cell: (r) => r.number },
              { header: "Cliente", cell: (r) => name(r.customer_id) },
              { header: "Medio", cell: (r) => label(r.method) },
              { header: "Monto", cell: (r) => money(r.amount), align: "right" },
              { header: "Sin aplicar", cell: (r) => money(r.unapplied_amount), align: "right" },
              { header: "Estado", cell: (r) => label(r.status) },
              ...(can("receivables:write") ? [{ header: "", cell: (r: Receipt) => r.status === "POSTED"
                ? <SmallButton tone="danger" disabled={action.busy} onClick={() => voidReceipt(r)}>Anular</SmallButton> : null }] : []),
            ]}
          />
        </Panel>
      </div>
    </>
  );
}
