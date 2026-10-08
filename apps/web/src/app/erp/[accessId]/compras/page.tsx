"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, quantity, type Page, type Party, type Product, type PurchaseOrder, type Warehouse } from "@/lib/erp";

const LIMIT = 25;
type Line = { product_id: string; quantity: string; unit_price: string };

export default function ComprasPage() {
  const { client, can } = useErp();
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState({ supplier_id: "", warehouse_id: "" });
  const [lines, setLines] = useState<Line[]>([{ product_id: "", quantity: "1", unit_price: "" }]);
  const [receiveQty, setReceiveQty] = useState<Record<number, string>>({});
  const action = useAction();

  const refs = useLoad(async () => ({
    suppliers: (await client.get<Page<Party>>("/parties", { role: "supplier", limit: 200 })).items.filter((p) => p.is_active),
    products: (await client.get<Page<Product>>("/products", { active: true, limit: 200 })).items,
    warehouses: (await client.get<Warehouse[]>("/warehouses")).filter((w) => w.is_active),
  }), []);
  const list = useLoad(() => client.get<Page<PurchaseOrder>>("/purchases/orders", { limit: LIMIT, offset }), [offset]);
  const detail = useLoad(() => (selected ? client.get<PurchaseOrder>(`/purchases/orders/${selected}`) : Promise.resolve(null)), [selected]);

  const supplier = (id: string) => refs.data?.suppliers.find((s) => s.id === id)?.legal_name ?? "-";

  async function create(e: React.FormEvent) {
    e.preventDefault();
    let created: PurchaseOrder | null = null;
    const ok = await action.run(async () => {
      created = await client.post<PurchaseOrder>("/purchases/orders", { ...draft, lines: lines.filter((l) => l.product_id) });
    }, "Orden de compra creada en borrador.");
    if (ok && created) {
      setLines([{ product_id: "", quantity: "1", unit_price: "" }]);
      setSelected((created as PurchaseOrder).id);
      list.reload();
    }
  }

  async function act(path: string, body?: unknown, ok?: string) {
    if (selected && (await action.run(() => client.post(`/purchases/orders/${selected}/${path}`, body, true), ok))) {
      setReceiveQty({});
      detail.reload();
      list.reload();
    }
  }

  const o = detail.data;
  const pending = o ? o.lines.filter((l) => Number(l.quantity_received) < Number(l.quantity)) : [];

  return (
    <>
      <PageTitle title="Compras" subtitle="Orden de compra -> recepcion (entra stock al costo neto de IVA) -> factura del proveedor" />
      {(refs.error || list.error || detail.error) && <Notice>{refs.error ?? list.error ?? detail.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {can("purchases:write") && (
        <Panel title="Nueva orden de compra" className="mb-5">
          <form onSubmit={create} className="flex flex-col gap-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
              <Field label="Proveedor">
                <Select required value={draft.supplier_id} onChange={(e) => setDraft({ ...draft, supplier_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {(refs.data?.suppliers ?? []).map((s) => <option key={s.id} value={s.id}>{s.legal_name}</option>)}
                </Select>
              </Field>
              <Field label="Deposito de recepcion">
                <Select required value={draft.warehouse_id} onChange={(e) => setDraft({ ...draft, warehouse_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {(refs.data?.warehouses ?? []).map((w) => <option key={w.id} value={w.id}>{w.code} - {w.name}</option>)}
                </Select>
              </Field>
            </div>
            {lines.map((l, i) => (
              <div key={i} className="grid grid-cols-1 gap-3 md:grid-cols-[3fr_1fr_1fr_auto]">
                <Select required={i === 0} value={l.product_id} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, product_id: e.target.value } : x)))}>
                  <option value="">Producto...</option>
                  {(refs.data?.products ?? []).map((p) => <option key={p.id} value={p.id}>{p.sku} - {p.name}</option>)}
                </Select>
                <Input type="number" min="0.0001" step="0.0001" placeholder="Cantidad" value={l.quantity} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, quantity: e.target.value } : x)))} />
                <Input type="number" min="0" step="1" required={i === 0} placeholder="Precio IVA incl." value={l.unit_price} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, unit_price: e.target.value } : x)))} />
                <SmallButton onClick={() => setLines(lines.length > 1 ? lines.filter((_, j) => j !== i) : lines)}>Quitar</SmallButton>
              </div>
            ))}
            <div className="flex gap-3">
              <SmallButton onClick={() => setLines([...lines, { product_id: "", quantity: "1", unit_price: "" }])}>+ Linea</SmallButton>
              <SmallButton type="submit" tone="primary" disabled={action.busy}>Crear orden</SmallButton>
            </div>
          </form>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1fr_1.2fr]">
        <Panel title="Ordenes de compra">
          <Table
            rows={list.data?.items ?? []}
            rowKey={(r) => r.id}
            columns={[
              { header: "Numero", cell: (r) => <button className="text-nx-accent hover:underline" onClick={() => setSelected(r.id)}>{r.number}</button> },
              { header: "Fecha", cell: (r) => day(r.created_at) },
              { header: "Proveedor", cell: (r) => supplier(r.supplier_id) },
              { header: "Estado", cell: (r) => label(r.status) },
              { header: "Total", cell: (r) => money(r.total), align: "right" },
            ]}
          />
          <Pager total={list.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
        </Panel>

        <Panel title={o ? `Orden ${o.number}` : "Detalle"}>
          {!o ? <p className="text-sm text-nx-muted">Elegir una orden.</p> : (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-nx-muted">{supplier(o.supplier_id)} · <strong className="text-nx-text">{label(o.status)}</strong></p>
              <Table
                rows={o.lines}
                rowKey={(l) => String(l.line_no)}
                columns={[
                  { header: "Descripcion", cell: (l) => l.description },
                  { header: "Pedido", cell: (l) => quantity(l.quantity), align: "right" },
                  { header: "Recibido", cell: (l) => quantity(l.quantity_received), align: "right" },
                  { header: "Precio", cell: (l) => money(l.unit_price), align: "right" },
                  { header: "Total", cell: (l) => money(l.line_total), align: "right" },
                  ...(can("purchases:receive") && ["CONFIRMED", "PARTIALLY_RECEIVED"].includes(o.status) ? [{
                    header: "Recibir", cell: (l: PurchaseOrder["lines"][number]) => Number(l.quantity_received) < Number(l.quantity) ? (
                      <Input type="number" min="0" step="0.0001" className="w-24"
                        max={String(Number(l.quantity) - Number(l.quantity_received))}
                        value={receiveQty[l.line_no] ?? ""} onChange={(e) => setReceiveQty({ ...receiveQty, [l.line_no]: e.target.value })} />
                    ) : null,
                  }] : []),
                ]}
              />
              <p className="text-right font-bold">Total: {money(o.total)}</p>
              {(
                <div className="flex flex-wrap justify-end gap-2">
                  {o.status === "DRAFT" && can("purchases:write") && <SmallButton tone="primary" disabled={action.busy} onClick={() => act("confirm", undefined, "Orden confirmada.")}>Confirmar</SmallButton>}
                  {["CONFIRMED", "PARTIALLY_RECEIVED"].includes(o.status) && can("purchases:receive") && (
                    <>
                      <SmallButton disabled={action.busy} onClick={() => setReceiveQty(Object.fromEntries(pending.map((l) => [l.line_no, String(Number(l.quantity) - Number(l.quantity_received))])))}>Completar pendientes</SmallButton>
                      <SmallButton tone="primary" disabled={action.busy} onClick={() => {
                        const items = Object.entries(receiveQty).filter(([, q]) => Number(q) > 0).map(([n, q]) => ({ line_no: Number(n), quantity: q }));
                        if (items.length) act("receive", { lines: items }, "Recepcion registrada: stock actualizado.");
                      }}>Registrar recepcion</SmallButton>
                    </>
                  )}
                  {!["RECEIVED", "CLOSED", "CANCELLED"].includes(o.status) && can("purchases:write") && (
                    <SmallButton tone="danger" disabled={action.busy} onClick={() => {
                      const reason = window.prompt("Motivo para cerrar/cancelar la orden:");
                      if (reason) act("close", { reason }, "Orden cerrada.");
                    }}>Cerrar / cancelar</SmallButton>
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
