"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, quantity, type Page, type Party, type Product, type SalesOrder, type SalesOrderSummary, type Warehouse } from "@/lib/erp";

const LIMIT = 25;
type Line = { product_id: string; quantity: string; discount_pct: string };

export default function VentasPage() {
  const { client, can } = useErp();
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState({ customer_id: "", warehouse_id: "", payment_condition: "CASH", notes: "" });
  const [lines, setLines] = useState<Line[]>([{ product_id: "", quantity: "1", discount_pct: "0" }]);
  const action = useAction();

  const refs = useLoad(async () => ({
    customers: (await client.get<Page<Party>>("/parties", { role: "customer", limit: 200 })).items.filter((p) => p.is_active),
    products: (await client.get<Page<Product>>("/products", { active: true, limit: 200 })).items,
    warehouses: (await client.get<Warehouse[]>("/warehouses")).filter((w) => w.is_active),
  }), []);
  const list = useLoad(() => client.get<Page<SalesOrderSummary>>("/sales/orders", { status, limit: LIMIT, offset }), [status, offset]);
  const detail = useLoad(() => (selected ? client.get<SalesOrder>(`/sales/orders/${selected}`) : Promise.resolve(null)), [selected]);

  const customer = (id: string) => refs.data?.customers.find((c) => c.id === id)?.legal_name ?? "-";
  const product = (id: string) => refs.data?.products.find((p) => p.id === id);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const body = { ...draft, notes: draft.notes || null,
      lines: lines.filter((l) => l.product_id).map((l) => ({ ...l, discount_pct: l.discount_pct || "0" })) };
    let created: SalesOrder | null = null;
    const ok = await action.run(async () => { created = await client.post<SalesOrder>("/sales/orders", body); }, "Pedido creado en borrador.");
    if (ok && created) {
      setLines([{ product_id: "", quantity: "1", discount_pct: "0" }]);
      setSelected((created as SalesOrder).id);
      list.reload();
    }
  }

  async function act(path: string, body?: unknown, ok?: string) {
    if (!selected) return;
    if (await action.run(() => client.post(`/sales/orders/${selected}/${path}`, body, true), ok)) {
      detail.reload();
      list.reload();
    }
  }

  const o = detail.data;
  const preview = lines.reduce((acc, l) => {
    const p = product(l.product_id);
    return p ? acc + Number(l.quantity || 0) * Number(p.sale_price) * (1 - Number(l.discount_pct || 0) / 100) : acc;
  }, 0);

  return (
    <>
      <PageTitle title="Ventas" subtitle="Pedido -> confirmacion (reserva stock) -> entrega (descuenta stock) -> factura interna" />
      {(refs.error || list.error || detail.error) && <Notice>{refs.error ?? list.error ?? detail.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {can("sales:write") && (
        <Panel title="Nuevo pedido" className="mb-5">
          <form onSubmit={create} className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
              <Field label="Cliente">
                <Select required value={draft.customer_id} onChange={(e) => setDraft({ ...draft, customer_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {(refs.data?.customers ?? []).map((c) => <option key={c.id} value={c.id}>{c.legal_name}</option>)}
                </Select>
              </Field>
              <Field label="Deposito">
                <Select required value={draft.warehouse_id} onChange={(e) => setDraft({ ...draft, warehouse_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {(refs.data?.warehouses ?? []).map((w) => <option key={w.id} value={w.id}>{w.code} - {w.name}</option>)}
                </Select>
              </Field>
              <Field label="Condicion">
                <Select value={draft.payment_condition} onChange={(e) => setDraft({ ...draft, payment_condition: e.target.value })}>
                  <option value="CASH">Contado</option>
                  <option value="CREDIT">Credito</option>
                </Select>
              </Field>
              <Field label="Notas"><Input value={draft.notes} onChange={(e) => setDraft({ ...draft, notes: e.target.value })} /></Field>
            </div>
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-1 gap-3 md:grid-cols-[3fr_1fr_1fr_auto]">
                <Select required={i === 0} value={l.product_id} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, product_id: e.target.value } : x)))}>
                  <option value="">Producto...</option>
                  {(refs.data?.products ?? []).map((p) => <option key={p.id} value={p.id}>{p.sku} - {p.name} ({money(p.sale_price)})</option>)}
                </Select>
                <Input type="number" min="0.0001" step="0.0001" placeholder="Cantidad" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
                <Input type="number" min="0" max="100" step="0.01" placeholder="Desc. %" value={l.discount_pct} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, discount_pct: e.target.value } : x)))} />
                <SmallButton onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : lines)}>Quitar</SmallButton>
              </div>
            ))}
            <div className="flex flex-wrap items-center gap-3">
              <SmallButton onClick={() => setLines([...lines, { product_id: "", quantity: "1", discount_pct: "0" }])}>+ Linea</SmallButton>
              <span className="text-sm text-nx-muted">Total estimado: {money(Math.round(preview))} (el servidor recalcula)</span>
              <SmallButton type="submit" tone="primary" disabled={action.busy}>Crear pedido</SmallButton>
            </div>
          </form>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_1.2fr]">
        <Panel title="Pedidos">
          <div className="mb-3 max-w-xs">
            <Select value={status} onChange={(e) => { setOffset(0); setStatus(e.target.value); }}>
              <option value="">Todos los estados</option>
              {["DRAFT", "CONFIRMED", "DELIVERED", "CANCELLED"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
            </Select>
          </div>
          <Table
            rows={list.data?.items ?? []}
            rowKey={(r) => r.id}
            columns={[
              { header: "Numero", cell: (r) => <button className="text-nx-accent hover:underline" onClick={() => setSelected(r.id)}>{r.number}</button> },
              { header: "Fecha", cell: (r) => day(r.created_at) },
              { header: "Cliente", cell: (r) => customer(r.customer_id) },
              { header: "Estado", cell: (r) => label(r.status) },
              { header: "Total", cell: (r) => money(r.total), align: "right" },
            ]}
          />
          <Pager total={list.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
        </Panel>

        <Panel title={o ? `Pedido ${o.number}` : "Detalle"}>
          {!o ? <p className="text-sm text-nx-muted">Elegir un pedido de la lista.</p> : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-nx-muted">
                {customer(o.customer_id)} · {label(o.payment_condition)} · <strong className="text-nx-text">{label(o.status)}</strong>
                {o.cancel_reason && ` · Motivo: ${o.cancel_reason}`}
              </p>
              <Table
                rows={o.lines}
                rowKey={(l) => String(l.line_no)}
                columns={[
                  { header: "Descripcion", cell: (l) => l.description },
                  { header: "Cant.", cell: (l) => quantity(l.quantity), align: "right" },
                  { header: "Precio", cell: (l) => money(l.unit_price), align: "right" },
                  { header: "Desc.", cell: (l) => `${Number(l.discount_pct)}%`, align: "right" },
                  { header: "IVA", cell: (l) => `${Number(l.tax_rate)}%`, align: "right" },
                  { header: "Total", cell: (l) => money(l.line_total), align: "right" },
                ]}
              />
              <div className="text-right text-sm">
                <p>Neto: {money(o.subtotal_net)} · IVA: {money(o.tax_total)}</p>
                <p className="text-lg font-bold">Total: {money(o.total)}</p>
              </div>
              {(
                <div className="flex flex-wrap justify-end gap-2">
                  {o.status === "DRAFT" && can("sales:write") && <SmallButton tone="primary" disabled={action.busy} onClick={() => act("confirm", undefined, "Pedido confirmado: stock reservado.")}>Confirmar</SmallButton>}
                  {o.status === "CONFIRMED" && can("sales:deliver") && <SmallButton tone="primary" disabled={action.busy} onClick={() => act("deliver", undefined, "Pedido entregado.")}>Entregar</SmallButton>}
                  {o.status === "DELIVERED" && can("receivables:write") && <SmallButton tone="primary" disabled={action.busy} onClick={() => act("invoice", undefined, "Factura interna emitida (ver Facturas y cobros).")}>Facturar</SmallButton>}
                  {(o.status === "DRAFT" || o.status === "CONFIRMED") && can("sales:write") && (
                    <SmallButton tone="danger" disabled={action.busy} onClick={() => {
                      const reason = window.prompt("Motivo de la cancelacion:");
                      if (reason) act("cancel", { reason }, "Pedido cancelado.");
                    }}>Cancelar</SmallButton>
                  )}
                </div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
