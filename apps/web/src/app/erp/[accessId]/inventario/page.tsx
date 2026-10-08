"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, quantity, type Balance, type Movement, type Page, type Product, type Warehouse } from "@/lib/erp";

const LIMIT = 25;
type Op = "receipts" | "issues" | "adjustments" | "transfers";

export default function InventarioPage() {
  const { client, ctx } = useErp();
  const [productId, setProductId] = useState("");
  const [offset, setOffset] = useState(0);
  const [mOffset, setMOffset] = useState(0);
  const [op, setOp] = useState<Op>("receipts");
  const [form, setForm] = useState({ product_id: "", warehouse_id: "", to_warehouse_id: "", quantity: "", unit_cost: "", direction: "IN", reason: "" });
  const action = useAction();

  const refs = useLoad(async () => ({
    warehouses: await client.get<Warehouse[]>("/warehouses"),
    products: (await client.get<Page<Product>>("/products", { active: true, limit: 200 })).items.filter((p) => p.tracks_stock),
  }), []);
  const balances = useLoad(() => client.get<Page<Balance>>("/stock/balances", { product_id: productId, limit: LIMIT, offset }), [productId, offset]);
  const kardex = useLoad(() => client.get<Page<Movement>>("/stock/movements", { product_id: productId, limit: LIMIT, offset: mOffset }), [productId, mOffset]);

  const wh = (id: string) => refs.data?.warehouses.find((w) => w.id === id)?.code ?? "-";
  const prod = (id: string) => refs.data?.products.find((p) => p.id === id)?.name ?? "-";

  function reloadAll() {
    balances.reload();
    kardex.reload();
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const body =
      op === "receipts" ? { product_id: form.product_id, warehouse_id: form.warehouse_id, quantity: form.quantity, unit_cost: form.unit_cost || "0" }
      : op === "issues" ? { product_id: form.product_id, warehouse_id: form.warehouse_id, quantity: form.quantity }
      : op === "adjustments" ? { product_id: form.product_id, warehouse_id: form.warehouse_id, quantity: form.quantity, direction: form.direction,
                                 unit_cost: form.unit_cost || null, reason: form.reason }
      : { product_id: form.product_id, from_warehouse_id: form.warehouse_id, to_warehouse_id: form.to_warehouse_id, quantity: form.quantity };
    if (await action.run(() => client.post(`/stock/${op}`, body, true), "Movimiento registrado.")) {
      setForm({ ...form, quantity: "", unit_cost: "", reason: "" });
      reloadAll();
    }
  }

  async function reverse(m: Movement) {
    const reason = window.prompt("Motivo de la reversion:");
    if (!reason) return;
    if (await action.run(() => client.post(`/stock/movements/${m.id}/reverse`, { reason }, true), "Movimiento revertido.")) reloadAll();
  }

  return (
    <>
      <PageTitle title="Inventario" subtitle="Costo promedio ponderado · el kardex no se edita: los errores se corrigen con una reversion" />
      {(refs.error || balances.error || kardex.error) && <Notice>{refs.error ?? balances.error ?? kardex.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {ctx.can_write && (
        <Panel title="Registrar movimiento" className="mb-5">
          <div className="mb-3 flex flex-wrap gap-2">
            {([["receipts", "Entrada"], ["issues", "Salida"], ["adjustments", "Ajuste"], ["transfers", "Transferencia"]] as [Op, string][]).map(([k, l]) => (
              <SmallButton key={k} tone={op === k ? "primary" : "default"} onClick={() => setOp(k)}>{l}</SmallButton>
            ))}
          </div>
          <form onSubmit={submit} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="Producto">
              <Select required value={form.product_id} onChange={(e) => setForm({ ...form, product_id: e.target.value })}>
                <option value="">Elegir...</option>
                {(refs.data?.products ?? []).map((p) => <option key={p.id} value={p.id}>{p.sku} - {p.name}</option>)}
              </Select>
            </Field>
            <Field label={op === "transfers" ? "Desde deposito" : "Deposito"}>
              <Select required value={form.warehouse_id} onChange={(e) => setForm({ ...form, warehouse_id: e.target.value })}>
                <option value="">Elegir...</option>
                {(refs.data?.warehouses ?? []).map((w) => <option key={w.id} value={w.id}>{w.code} - {w.name}</option>)}
              </Select>
            </Field>
            {op === "transfers" && (
              <Field label="Hacia deposito">
                <Select required value={form.to_warehouse_id} onChange={(e) => setForm({ ...form, to_warehouse_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {(refs.data?.warehouses ?? []).map((w) => <option key={w.id} value={w.id}>{w.code} - {w.name}</option>)}
                </Select>
              </Field>
            )}
            {op === "adjustments" && (
              <Field label="Sentido">
                <Select value={form.direction} onChange={(e) => setForm({ ...form, direction: e.target.value })}>
                  <option value="IN">Aumenta (+)</option>
                  <option value="OUT">Disminuye (-)</option>
                </Select>
              </Field>
            )}
            <Field label="Cantidad"><Input type="number" min="0.0001" step="0.0001" required value={form.quantity} onChange={(e) => setForm({ ...form, quantity: e.target.value })} /></Field>
            {(op === "receipts" || (op === "adjustments" && form.direction === "IN")) && (
              <Field label="Costo unitario (Gs., neto de IVA)">
                <Input type="number" min="0" step="0.01" required={op === "receipts"} value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} />
              </Field>
            )}
            {op === "adjustments" && (
              <Field label="Motivo"><Input required minLength={3} value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></Field>
            )}
            <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar</SmallButton></div>
          </form>
        </Panel>
      )}

      <div className="mb-3 max-w-sm">
        <Select value={productId} onChange={(e) => { setProductId(e.target.value); setOffset(0); setMOffset(0); }}>
          <option value="">Todos los productos</option>
          {(refs.data?.products ?? []).map((p) => <option key={p.id} value={p.id}>{p.sku} - {p.name}</option>)}
        </Select>
      </div>

      <Panel title="Saldos" className="mb-5">
        <Table
          rows={balances.data?.items ?? []}
          rowKey={(b) => `${b.product_id}-${b.warehouse_id}`}
          empty="Sin stock registrado."
          columns={[
            { header: "Producto", cell: (b) => `${b.sku} - ${b.product_name}` },
            { header: "Deposito", cell: (b) => b.warehouse_code },
            { header: "Existencia", cell: (b) => quantity(b.on_hand), align: "right" },
            { header: "Reservado", cell: (b) => quantity(b.reserved), align: "right" },
            { header: "Disponible", cell: (b) => quantity(b.available), align: "right" },
            { header: "Costo prom.", cell: (b) => money(b.average_cost), align: "right" },
            { header: "Valor", cell: (b) => money(b.stock_value), align: "right" },
          ]}
        />
        <Pager total={balances.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
      </Panel>

      <Panel title="Kardex">
        <Table
          rows={kardex.data?.items ?? []}
          rowKey={(m) => m.id}
          columns={[
            { header: "Fecha", cell: (m) => day(m.created_at) },
            { header: "Tipo", cell: (m) => label(m.movement_type) },
            { header: "Producto", cell: (m) => prod(m.product_id) },
            { header: "Deposito", cell: (m) => wh(m.warehouse_id) },
            { header: "Cantidad", cell: (m) => `${m.direction > 0 ? "+" : "-"}${quantity(m.quantity)}`, align: "right" },
            { header: "Costo unit.", cell: (m) => money(m.unit_cost), align: "right" },
            { header: "Referencia", cell: (m) => m.reference ?? m.notes ?? "-" },
            ...(ctx.can_write ? [{ header: "", cell: (m: Movement) =>
              m.movement_type !== "REVERSAL" && !m.reference?.startsWith("OV-") && !m.reference?.startsWith("OC-")
                ? <SmallButton tone="danger" onClick={() => reverse(m)} disabled={action.busy}>Revertir</SmallButton> : null }] : []),
          ]}
        />
        <Pager total={kardex.data?.total ?? 0} limit={LIMIT} offset={mOffset} onChange={setMOffset} />
      </Panel>
    </>
  );
}
