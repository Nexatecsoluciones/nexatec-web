"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { label, money, type Page, type Party } from "@/lib/erp";

const LIMIT = 25;
const EMPTY = { legal_name: "", ruc: "", ruc_dv: "", is_customer: true, is_supplier: false, email: "", phone: "",
  payment_terms_days: "0", credit_limit: "0" };

export default function TercerosPage() {
  const { client, can } = useErp();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [offset, setOffset] = useState(0);
  const [form, setForm] = useState(EMPTY);
  const action = useAction();
  const list = useLoad(() => client.get<Page<Party>>("/parties", { q, role, limit: LIMIT, offset }), [q, role, offset]);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const ok = await action.run(() => client.post("/parties", {
      ...form, ruc: form.ruc || null, ruc_dv: form.ruc_dv || null, email: form.email || null, phone: form.phone || null,
      payment_terms_days: Number(form.payment_terms_days), credit_limit: form.credit_limit || "0",
    }), "Guardado.");
    if (ok) {
      setForm(EMPTY);
      list.reload();
    }
  }

  return (
    <>
      <PageTitle title="Clientes y proveedores" subtitle="Un mismo tercero puede ser cliente y proveedor" />
      {list.error && <Notice>{list.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {can("parties:write") && (
        <Panel title="Nuevo tercero" className="mb-5">
          <form onSubmit={create} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="Razon social"><Input required minLength={2} value={form.legal_name} onChange={(e) => setForm({ ...form, legal_name: e.target.value })} /></Field>
            <Field label="RUC (sin DV)"><Input inputMode="numeric" pattern="[0-9]{1,8}" value={form.ruc} onChange={(e) => setForm({ ...form, ruc: e.target.value })} /></Field>
            <Field label="DV"><Input inputMode="numeric" pattern="[0-9]" maxLength={1} value={form.ruc_dv} onChange={(e) => setForm({ ...form, ruc_dv: e.target.value })} /></Field>
            <Field label="Email"><Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Telefono"><Input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></Field>
            <Field label="Plazo de pago (dias)"><Input type="number" min="0" max="365" value={form.payment_terms_days} onChange={(e) => setForm({ ...form, payment_terms_days: e.target.value })} /></Field>
            <Field label="Limite de credito (Gs.)"><Input type="number" min="0" value={form.credit_limit} onChange={(e) => setForm({ ...form, credit_limit: e.target.value })} /></Field>
            <div className="flex items-end gap-4 text-sm">
              <label className="flex items-center gap-2"><input type="checkbox" checked={form.is_customer} onChange={(e) => setForm({ ...form, is_customer: e.target.checked })} /> Cliente</label>
              <label className="flex items-center gap-2"><input type="checkbox" checked={form.is_supplier} onChange={(e) => setForm({ ...form, is_supplier: e.target.checked })} /> Proveedor</label>
              <SmallButton type="submit" tone="primary" disabled={action.busy}>Guardar</SmallButton>
            </div>
          </form>
          <p className="mt-3 text-xs text-nx-muted">El RUC se valida por digito verificador (formato). Eso no confirma que el contribuyente exista.</p>
        </Panel>
      )}

      <Panel>
        <div className="mb-3 flex max-w-xl gap-2">
          <Input placeholder="Buscar por nombre o RUC" value={q} onChange={(e) => { setOffset(0); setQ(e.target.value); }} />
          <Select value={role} onChange={(e) => { setOffset(0); setRole(e.target.value); }}>
            <option value="">Todos</option>
            <option value="customer">Clientes</option>
            <option value="supplier">Proveedores</option>
          </Select>
        </div>
        <Table
          rows={list.data?.items ?? []}
          rowKey={(p) => p.id}
          columns={[
            { header: "Razon social", cell: (p) => p.legal_name },
            { header: "RUC", cell: (p) => (p.ruc ? `${p.ruc}-${p.ruc_dv}` : "-") },
            { header: "RUC estado", cell: (p) => label(p.ruc_status) },
            { header: "Rol", cell: (p) => [p.is_customer && "Cliente", p.is_supplier && "Proveedor"].filter(Boolean).join(" / ") },
            { header: "Plazo", cell: (p) => `${p.payment_terms_days} d`, align: "right" },
            { header: "Limite credito", cell: (p) => money(p.credit_limit), align: "right" },
          ]}
        />
        <Pager total={list.data?.total ?? 0} limit={LIMIT} offset={offset} onChange={setOffset} />
      </Panel>
    </>
  );
}
