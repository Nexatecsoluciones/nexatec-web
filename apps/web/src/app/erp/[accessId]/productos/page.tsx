"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { label, money, type Category, type Page, type Product, type Tax, type Unit } from "@/lib/erp";

const LIMIT = 25;
const EMPTY = { sku: "", name: "", product_type: "GOOD", unit_id: "", tax_code: "IVA10", sale_price: "", category_id: "" };

export default function ProductosPage() {
  const { client, can } = useErp();
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState(EMPTY);
  const action = useAction();

  const list = useLoad(() => client.get<Page<Product>>("/products", { q, limit: LIMIT, offset }), [q, offset]);
  const refs = useLoad(async () => ({
    units: await client.get<Unit[]>("/units"),
    taxes: await client.get<Tax[]>("/taxes"),
    categories: await client.get<Category[]>("/product-categories"),
  }), []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const ok = await action.run(() => client.post("/products", {
      ...form, category_id: form.category_id || null, sale_price: form.sale_price || "0",
      tracks_stock: form.product_type === "GOOD",
    }), "Producto creado.");
    if (ok) {
      setForm(EMPTY);
      list.reload();
    }
  }

  async function toggle(p: Product) {
    if (await action.run(() => client.patch(`/products/${p.id}`, { is_active: !p.is_active }))) list.reload();
  }

  const units = refs.data?.units ?? [];
  const cat = (id: string | null) => refs.data?.categories.find((c) => c.id === id)?.name ?? "-";

  return (
    <>
      <PageTitle title="Productos y servicios" subtitle="Precios IVA incluido" />
      {(list.error || refs.error) && <Notice>{list.error ?? refs.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {can("products:write") && (
        <Panel title="Nuevo producto" className="mb-5">
          <form onSubmit={create} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="SKU"><Input required value={form.sku} onChange={(e) => setForm({ ...form, sku: e.target.value })} /></Field>
            <Field label="Nombre"><Input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
            <Field label="Tipo">
              <Select value={form.product_type} onChange={(e) => setForm({ ...form, product_type: e.target.value })}>
                <option value="GOOD">Bien (maneja stock)</option>
                <option value="SERVICE">Servicio</option>
              </Select>
            </Field>
            <Field label="Unidad">
              <Select required value={form.unit_id} onChange={(e) => setForm({ ...form, unit_id: e.target.value })}>
                <option value="">Elegir...</option>
                {units.map((u) => <option key={u.id} value={u.id}>{u.code} - {u.name}</option>)}
              </Select>
            </Field>
            <Field label="IVA">
              <Select value={form.tax_code} onChange={(e) => setForm({ ...form, tax_code: e.target.value })}>
                {(refs.data?.taxes ?? []).map((t) => <option key={t.code} value={t.code}>{t.name}</option>)}
              </Select>
            </Field>
            <Field label="Categoria">
              <Select value={form.category_id} onChange={(e) => setForm({ ...form, category_id: e.target.value })}>
                <option value="">Sin categoria</option>
                {(refs.data?.categories ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
            <Field label="Precio de venta (Gs., IVA incl.)">
              <Input type="number" min="0" step="1" required value={form.sale_price} onChange={(e) => setForm({ ...form, sale_price: e.target.value })} />
            </Field>
            <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Guardar</SmallButton></div>
          </form>
        </Panel>
      )}

      <Panel>
        <div className="mb-3 max-w-sm">
          <Input placeholder="Buscar por nombre o SKU" value={q} onChange={(e) => { setOffset(0); setQ(e.target.value); }} />
        </div>
        <Table
          rows={list.data?.items ?? []}
          rowKey={(p) => p.id}
          columns={[
            { header: "SKU", cell: (p) => p.sku },
            { header: "Nombre", cell: (p) => p.name },
            { header: "Tipo", cell: (p) => label(p.product_type) },
            { header: "Categoria", cell: (p) => cat(p.category_id) },
            { header: "IVA", cell: (p) => p.tax_code },
            { header: "Precio", cell: (p) => money(p.sale_price), align: "right" },
            { header: "Estado", cell: (p) => (p.is_active ? "Activo" : "Inactivo") },
            ...(can("products:write") ? [{ header: "", cell: (p: Product) => (
              <SmallButton onClick={() => toggle(p)} disabled={action.busy}>{p.is_active ? "Desactivar" : "Activar"}</SmallButton>
            ) }] : []),
          ]}
        />
        <Pager total={list.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
      </Panel>
    </>
  );
}
