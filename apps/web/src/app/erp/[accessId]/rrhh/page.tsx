"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, money, type HrCategory, type HrEmployee, type Page } from "@/lib/erp";
import { HrTabs, todayISO, useHrRefs } from "./shared";

const EMPTY = { national_id: "", last_names: "", first_names: "", category_id: "", trade: "", phone: "", site_id: "",
  start_date: "", hourly_rate: "", pay_method: "CASH", ips_entry: "" };

interface Assignment { id: string; site_id: string; start_date: string; end_date: string | null; notes: string | null }

export default function PersonalPage() {
  const { client, can } = useErp();
  const write = can("hr:write");
  const amounts = can("hr:amounts");
  const [q, setQ] = useState("");
  const [siteFilter, setSiteFilter] = useState("");
  const [active, setActive] = useState(true);
  const [form, setForm] = useState(EMPTY);
  const [selected, setSelected] = useState<string | null>(null);
  const [edit, setEdit] = useState<Partial<HrEmployee>>({});
  const [move, setMove] = useState({ site_id: "", start_date: todayISO() });
  const action = useAction();

  const refs = useHrRefs();
  const cats = useLoad(() => client.get<HrCategory[]>("/hr/categories"), []);
  const list = useLoad(() => client.get<Page<HrEmployee>>("/hr/employees", { q, site_id: siteFilter, active, limit: 500 }), [q, siteFilter, active]);
  const detail = useLoad(async () => {
    if (!selected) return null;
    const [emp, hist] = await Promise.all([
      client.get<HrEmployee>(`/hr/employees/${selected}`),
      client.get<Assignment[]>(`/hr/employees/${selected}/assignments`),
    ]);
    setEdit(emp);
    return { emp, hist };
  }, [selected]);

  const site = (id: string | null) => refs.data?.sites.find((s) => s.id === id)?.name ?? "-";
  const reload = () => { list.reload(); detail.reload(); };

  async function create(e: React.FormEvent) {
    e.preventDefault();
    const body: Record<string, unknown> = Object.fromEntries(Object.entries(form).filter(([, v]) => v !== ""));
    if (!amounts) delete body.hourly_rate;
    let created: HrEmployee | null = null;
    if (await action.run(async () => { created = await client.post<HrEmployee>("/hr/employees", body); }, "Empleado dado de alta.")) {
      setForm(EMPTY);
      list.reload();
      if (created) setSelected((created as HrEmployee).id);
    }
  }

  async function save() {
    if (!selected) return;
    const keys = ["last_names", "first_names", "trade", "phone", "email", "address", "neighborhood", "city", "birth_date",
      "ips_entry", "ips_notes", "family_notes", "training", "skills", "references_notes", "tracks_attendance", "pay_method",
      ...(amounts ? ["hourly_rate", "bonus_per_hour", "bank_account"] : [])] as const;
    const body = Object.fromEntries(keys.map((k) => [k, (edit as Record<string, unknown>)[k] === "" ? null : (edit as Record<string, unknown>)[k]]));
    if (await action.run(() => client.patch(`/hr/employees/${selected}`, body), "Ficha guardada.")) reload();
  }

  const e = detail.data?.emp;
  const f = (k: keyof HrEmployee) => (edit[k] ?? "") as string;
  const setF = (k: keyof HrEmployee, v: string | boolean) => setEdit({ ...edit, [k]: v });

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Personal, obras, asistencia, adelantos y planillas de pago" />
      <HrTabs />
      {(refs.error || list.error || detail.error) && <Notice>{refs.error ?? list.error ?? detail.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {write && (
        <Panel title="Alta de empleado" className="mb-5">
          <form onSubmit={create} className="grid grid-cols-1 gap-3 md:grid-cols-4">
            <Field label="C.I."><Input required minLength={5} value={form.national_id} onChange={(x) => setForm({ ...form, national_id: x.target.value })} /></Field>
            <Field label="Apellidos"><Input required minLength={2} value={form.last_names} onChange={(x) => setForm({ ...form, last_names: x.target.value })} /></Field>
            <Field label="Nombres"><Input required minLength={2} value={form.first_names} onChange={(x) => setForm({ ...form, first_names: x.target.value })} /></Field>
            <Field label="Celular"><Input value={form.phone} onChange={(x) => setForm({ ...form, phone: x.target.value })} /></Field>
            <Field label="Categoria">
              <Select value={form.category_id} onChange={(x) => {
                const c = cats.data?.find((k) => k.id === x.target.value);
                setForm({ ...form, category_id: x.target.value, trade: c?.default_trade ?? form.trade,
                  hourly_rate: c && !c.manual_rate && c.hourly_rate ? String(Number(c.hourly_rate)) : form.hourly_rate });
              }}>
                <option value="">Sin categoria</option>
                {(cats.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.code}</option>)}
              </Select>
            </Field>
            <Field label="Oficio"><Input value={form.trade} onChange={(x) => setForm({ ...form, trade: x.target.value })} /></Field>
            <Field label="Obra">
              <Select value={form.site_id} onChange={(x) => setForm({ ...form, site_id: x.target.value })}>
                <option value="">Sin asignar</option>
                {(refs.data?.sites ?? []).filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
              </Select>
            </Field>
            <Field label="Desde"><Input type="date" value={form.start_date} onChange={(x) => setForm({ ...form, start_date: x.target.value })} /></Field>
            {amounts && <Field label="Jornal por hora (Gs.)"><Input type="number" min="0" step="1" value={form.hourly_rate} onChange={(x) => setForm({ ...form, hourly_rate: x.target.value })} /></Field>}
            <Field label="Cobra en">
              <Select value={form.pay_method} onChange={(x) => setForm({ ...form, pay_method: x.target.value })}>
                <option value="CASH">Efectivo</option><option value="TRANSFER">Transferencia</option>
              </Select>
            </Field>
            <Field label="Entrada IPS"><Input type="date" value={form.ips_entry} onChange={(x) => setForm({ ...form, ips_entry: x.target.value })} /></Field>
            <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Dar de alta</SmallButton></div>
          </form>
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        <Panel title={`Personal (${list.data?.total ?? 0})`}>
          <div className="mb-3 grid grid-cols-1 gap-2 md:grid-cols-[2fr_2fr_auto]">
            <Input placeholder="Buscar por nombre, C.I. u oficio" value={q} onChange={(x) => setQ(x.target.value)} />
            <Select value={siteFilter} onChange={(x) => setSiteFilter(x.target.value)}>
              <option value="">Todas las obras</option>
              {(refs.data?.sites ?? []).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
            </Select>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={active} onChange={(x) => setActive(x.target.checked)} /> Activos</label>
          </div>
          <Table compact rows={list.data?.items ?? []} rowKey={(r) => r.id} empty="Sin empleados." columns={[
            { header: "Empleado", cell: (r) => (
              <button className="text-left" onClick={() => setSelected(r.id)}>
                <p className={`font-semibold ${selected === r.id ? "text-nx-accent" : "hover:text-nx-accent"}`}>{r.full_name}</p>
                <p className="text-xs text-nx-muted">C.I. {r.national_id}{r.trade ? ` · ${r.trade}` : ""}</p>
              </button>
            ) },
            { header: "Obra", cell: (r) => site(r.current_site_id) },
            ...(amounts ? [{ header: "Jornal/h", cell: (r: HrEmployee) => money(r.hourly_rate), align: "right" as const }] : []),
          ]} />
        </Panel>

        <Panel title={e ? e.full_name : "Ficha"}>
          {!e ? <p className="text-sm text-nx-muted">Elegir un empleado.</p> : (
            <div className="flex flex-col gap-4 text-sm">
              <p className="text-nx-muted">C.I. {e.national_id} · {e.is_active ? "Activo" : "De baja"} · Obra actual: <strong className="text-nx-text">{site(e.current_site_id)}</strong>
                {e.ips_entry && <> · IPS desde {day(e.ips_entry)}{e.ips_exit && ` hasta ${day(e.ips_exit)}`}</>}</p>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="Apellidos"><Input disabled={!write} value={f("last_names")} onChange={(x) => setF("last_names", x.target.value)} /></Field>
                <Field label="Nombres"><Input disabled={!write} value={f("first_names")} onChange={(x) => setF("first_names", x.target.value)} /></Field>
                <Field label="Oficio"><Input disabled={!write} value={f("trade")} onChange={(x) => setF("trade", x.target.value)} /></Field>
                <Field label="Celular"><Input disabled={!write} value={f("phone")} onChange={(x) => setF("phone", x.target.value)} /></Field>
                <Field label="Email"><Input disabled={!write} type="email" value={f("email")} onChange={(x) => setF("email", x.target.value)} /></Field>
                <Field label="Fecha de nacimiento"><Input disabled={!write} type="date" value={f("birth_date")} onChange={(x) => setF("birth_date", x.target.value)} /></Field>
                <Field label="Direccion"><Input disabled={!write} value={f("address")} onChange={(x) => setF("address", x.target.value)} /></Field>
                <Field label="Barrio / ciudad">
                  <div className="grid grid-cols-2 gap-2">
                    <Input disabled={!write} value={f("neighborhood")} onChange={(x) => setF("neighborhood", x.target.value)} />
                    <Input disabled={!write} value={f("city")} onChange={(x) => setF("city", x.target.value)} />
                  </div>
                </Field>
                <Field label="Entrada IPS"><Input disabled={!write} type="date" value={f("ips_entry")} onChange={(x) => setF("ips_entry", x.target.value)} /></Field>
                <Field label="Cobra en">
                  <Select disabled={!write} value={f("pay_method")} onChange={(x) => setF("pay_method", x.target.value)}>
                    <option value="CASH">Efectivo</option><option value="TRANSFER">Transferencia</option>
                  </Select>
                </Field>
                {amounts && <>
                  <Field label="Jornal por hora (Gs.)"><Input disabled={!write} type="number" min="0" step="1" value={f("hourly_rate")} onChange={(x) => setF("hourly_rate", x.target.value)} /></Field>
                  <Field label="Premio por hora (Gs.)"><Input disabled={!write} type="number" min="0" step="1" value={f("bonus_per_hour")} onChange={(x) => setF("bonus_per_hour", x.target.value)} /></Field>
                  <Field label="Nro. de cuenta"><Input disabled={!write} value={f("bank_account")} onChange={(x) => setF("bank_account", x.target.value)} /></Field>
                </>}
                <label className="flex items-center gap-2"><input type="checkbox" disabled={!write} checked={!!edit.tracks_attendance} onChange={(x) => setF("tracks_attendance", x.target.checked)} /> Registra asistencia</label>
                <div className="md:col-span-2"><Field label="Formacion / habilidades"><Input disabled={!write} value={f("skills")} onChange={(x) => setF("skills", x.target.value)} /></Field></div>
                <div className="md:col-span-2"><Field label="Referencias"><Input disabled={!write} value={f("references_notes")} onChange={(x) => setF("references_notes", x.target.value)} /></Field></div>
              </div>
              {write && e.is_active && <div><SmallButton tone="primary" disabled={action.busy} onClick={save}>Guardar ficha</SmallButton></div>}

              {write && e.is_active && (
                <div className="grid grid-cols-1 items-end gap-2 rounded-xl border border-nx-line p-3 md:grid-cols-[2fr_1fr_auto]">
                  <Field label="Trasladar a">
                    <Select value={move.site_id} onChange={(x) => setMove({ ...move, site_id: x.target.value })}>
                      <option value="">Elegir obra...</option>
                      {(refs.data?.sites ?? []).filter((s) => s.is_active && s.id !== e.current_site_id).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
                    </Select>
                  </Field>
                  <Field label="Desde"><Input type="date" value={move.start_date} onChange={(x) => setMove({ ...move, start_date: x.target.value })} /></Field>
                  <SmallButton disabled={action.busy || !move.site_id} onClick={async () => {
                    if (await action.run(() => client.post(`/hr/employees/${e.id}/assign`, move), "Traslado registrado.")) reload();
                  }}>{e.current_site_id ? "Trasladar" : "Asignar"}</SmallButton>
                </div>
              )}

              <p className="font-bold">Historial de obras</p>
              <Table compact rows={detail.data?.hist ?? []} rowKey={(a) => a.id} empty="Sin asignaciones." columns={[
                { header: "Obra", cell: (a) => site(a.site_id) },
                { header: "Desde", cell: (a) => day(a.start_date) },
                { header: "Hasta", cell: (a) => (a.end_date ? day(a.end_date) : "Actual") },
              ]} />

              {write && e.is_active && (
                <div><SmallButton tone="danger" disabled={action.busy} onClick={async () => {
                  const exit = window.prompt("Fecha de baja (AAAA-MM-DD):", todayISO());
                  if (exit && await action.run(() => client.post(`/hr/employees/${e.id}/deactivate`, { exit_date: exit }), "Empleado dado de baja (se cerro su obra y su IPS).")) reload();
                }}>Dar de baja</SmallButton></div>
              )}
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
