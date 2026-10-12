"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { Barcode } from "@/components/barcode";
import { label, money, type Barcode as BarcodeT, type Category, type Page, type Product, type Tax, type Unit } from "@/lib/erp";

function BarcodesPanel({ product, onClose }: { product: Product; onClose: () => void }) {
  const { client, can } = useErp();
  const action = useAction();
  const [code, setCode] = useState("");
  const codes = useLoad(() => client.get<BarcodeT[]>(`/products/${product.id}/barcodes`), [product.id]);
  const write = can("products:write");
  return (
    <Panel title={`Codigos de barras · ${product.name}`} className="mb-5">
      {action.error && <Notice>{action.error}</Notice>}
      <div className="flex flex-wrap gap-4">
        {(codes.data ?? []).map((b) => (
          <div key={b.id} className={`rounded-xl border bg-white p-3 text-center text-black ${b.is_primary ? "border-nx-accent" : "border-nx-line"}`}>
            <Barcode value={b.code} symbology={b.symbology} height={36} width={1.4} />
            <p className="mt-1 text-[11px]">{b.symbology}{b.is_internal ? " · interno" : ""}{b.is_primary ? " · principal" : ""}</p>
            {write && <button className="text-[11px] text-red-600 hover:underline" onClick={async () => {
              if (window.confirm(`¿Quitar el codigo ${b.code}?`) && await action.run(() => client.del(`/products/${product.id}/barcodes/${b.id}`))) codes.reload();
            }}>Quitar</button>}
          </div>
        ))}
        {codes.data?.length === 0 && <p className="text-sm text-nx-muted">Sin codigos todavia.</p>}
      </div>
      {write && (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <Field label="Escanear o escribir el codigo del fabricante (EAN/UPC) u otro">
            <Input value={code} onChange={(e) => setCode(e.target.value)} onKeyDown={async (e) => {
              if (e.key === "Enter" && code.trim()) {
                e.preventDefault();
                if (await action.run(() => client.post(`/products/${product.id}/barcodes`, { code: code.trim(), primary: !codes.data?.length }))) { setCode(""); codes.reload(); }
              }
            }} />
          </Field>
          <SmallButton disabled={action.busy || !code.trim()} onClick={async () => {
            if (await action.run(() => client.post(`/products/${product.id}/barcodes`, { code: code.trim(), primary: !codes.data?.length }))) { setCode(""); codes.reload(); }
          }}>Agregar</SmallButton>
          <SmallButton tone="primary" disabled={action.busy} onClick={async () => {
            if (await action.run(() => client.post(`/products/${product.id}/barcodes/generate`))) codes.reload();
          }}>Generar codigo interno</SmallButton>
          <SmallButton onClick={onClose}>Cerrar</SmallButton>
        </div>
      )}
      <p className="mt-2 text-xs text-nx-muted">Los EAN/UPC se validan por digito verificador. Los internos (NX...) son Code 128: sirven dentro de tu negocio y no son codigos GS1.</p>
    </Panel>
  );
}

const LIMIT = 25;
const EMPTY = { sku: "", name: "", product_type: "GOOD", unit_id: "", tax_code: "IVA10", sale_price: "", category_id: "" };

export default function ProductosPage() {
  const { client, can, base } = useErp();
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [codesFor, setCodesFor] = useState<Product | null>(null);
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

      {codesFor && <BarcodesPanel product={codesFor} onClose={() => setCodesFor(null)} />}

      <Panel>
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <div className="w-full max-w-sm">
            <Input placeholder="Buscar por nombre o SKU" value={q} onChange={(e) => { setOffset(0); setQ(e.target.value); }} />
          </div>
          {picked.length > 0 && (
            <>
              <a className="rounded-full bg-nx-accent px-4 py-2 text-sm font-bold text-[#06352f]" target="_blank" rel="noopener noreferrer"
                href={`${base.replace("/erp/", "/imprimir/")}/etiquetas?ids=${picked.join(",")}`}>Imprimir etiquetas ({picked.length})</a>
              <SmallButton onClick={() => setPicked([])}>Limpiar seleccion</SmallButton>
            </>
          )}
        </div>
        <Table
          rows={list.data?.items ?? []}
          rowKey={(p) => p.id}
          columns={[
            { header: "", cell: (p) => <input type="checkbox" aria-label={`Elegir ${p.name} para etiquetas`} checked={picked.includes(p.id)}
                onChange={(e) => setPicked(e.target.checked ? [...picked, p.id] : picked.filter((x) => x !== p.id))} /> },
            { header: "SKU", cell: (p) => p.sku },
            { header: "Nombre", cell: (p) => <button className="text-left hover:text-nx-accent" title="Codigos de barras" onClick={() => setCodesFor(p)}>{p.name}</button> },
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
