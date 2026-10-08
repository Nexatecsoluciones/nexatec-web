"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, hhmm, WEEKDAYS, type HrDevice, type HrSite } from "@/lib/erp";
import { HrTabs } from "../shared";

type Form = {
  id?: string; code: string; name: string; client_name: string; location: string; start_time: string; end_time: string;
  workdays: number[]; tolerance_minutes: string; special: Record<number, { start_time: string; end_time: string }>; is_active: boolean;
  latitude: string; longitude: string; geofence_radius_m: string;
};

const EMPTY: Form = { code: "", name: "", client_name: "", location: "", start_time: "07:00", end_time: "15:00",
  workdays: [0, 1, 2, 3, 4], tolerance_minutes: "10", special: {}, is_active: true, latitude: "", longitude: "", geofence_radius_m: "200" };

function toForm(s: HrSite): Form {
  return {
    id: s.id, code: s.code, name: s.name, client_name: s.client_name ?? "", location: s.location ?? "",
    start_time: hhmm(s.start_time), end_time: hhmm(s.end_time), workdays: s.workdays, tolerance_minutes: String(s.tolerance_minutes),
    special: Object.fromEntries(s.day_schedules.map((d) => [d.weekday, { start_time: hhmm(d.start_time), end_time: hhmm(d.end_time) }])),
    is_active: s.is_active, latitude: s.latitude ?? "", longitude: s.longitude ?? "", geofence_radius_m: String(s.geofence_radius_m),
  };
}

export default function ObrasPage() {
  const { client, can, ctx } = useErp();
  const write = can("hr:write");
  const [form, setForm] = useState<Form>(EMPTY);
  const [dev, setDev] = useState({ site_id: "", name: "" });
  const [newLink, setNewLink] = useState<string | null>(null);
  const action = useAction();
  const sites = useLoad(() => client.get<HrSite[]>("/hr/sites"), []);
  const devices = useLoad(() => (write ? client.get<HrDevice[]>("/hr/devices") : Promise.resolve([] as HrDevice[])), []);
  const siteName = (id: string) => sites.data?.find((s) => s.id === id)?.name ?? "-";

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      code: form.code, name: form.name, client_name: form.client_name || null, location: form.location || null,
      start_time: form.start_time, end_time: form.end_time, workdays: form.workdays, tolerance_minutes: Number(form.tolerance_minutes),
      day_schedules: Object.entries(form.special).map(([wd, v]) => ({ weekday: Number(wd), ...v })), is_active: form.is_active,
      latitude: form.latitude ? Number(form.latitude).toFixed(6) : null, longitude: form.longitude ? Number(form.longitude).toFixed(6) : null,
      geofence_radius_m: Number(form.geofence_radius_m) || 200,
    };
    const ok = await action.run(() => (form.id ? client.put(`/hr/sites/${form.id}`, body) : client.post("/hr/sites", body)),
      form.id ? "Obra actualizada." : "Obra creada.");
    if (ok) { setForm(EMPTY); sites.reload(); }
  }

  const toggleDay = (d: number) => setForm({ ...form, workdays: form.workdays.includes(d) ? form.workdays.filter((x) => x !== d) : [...form.workdays, d].sort() });
  const toggleSpecial = (d: number) => {
    const special = { ...form.special };
    if (special[d]) delete special[d];
    else special[d] = { start_time: "07:00", end_time: "12:00" };
    setForm({ ...form, special });
  };

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Obras o centros de trabajo, con el horario de referencia para tardanzas y horas extra" />
      <HrTabs />
      {sites.error && <Notice>{sites.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Obras">
          <Table compact rows={sites.data ?? []} rowKey={(s) => s.id} empty="Todavia no hay obras." columns={[
            { header: "Obra", cell: (s) => (
              <button className="text-left" disabled={!write} onClick={() => setForm(toForm(s))}>
                <p className="font-semibold hover:text-nx-accent">{s.code} - {s.name}</p>
                <p className="text-xs text-nx-muted">{[s.client_name, s.location].filter(Boolean).join(" · ")}</p>
              </button>
            ) },
            { header: "Horario", cell: (s) => <>
              <p>{hhmm(s.start_time)} a {hhmm(s.end_time)} · {s.workdays.map((d) => WEEKDAYS[d]).join(" ")}</p>
              {s.day_schedules.map((d) => <p key={d.weekday} className="text-xs text-nx-muted">{WEEKDAYS[d.weekday]}: {hhmm(d.start_time)} a {hhmm(d.end_time)}</p>)}
            </> },
            { header: "Estado", cell: (s) => (s.is_active ? "Activa" : "Inactiva") },
          ]} />
        </Panel>

        {write && (
          <Panel title={form.id ? `Editar ${form.code}` : "Nueva obra"}>
            <form onSubmit={save} className="grid grid-cols-1 gap-3 md:grid-cols-2">
              <Field label="Codigo"><Input required value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} /></Field>
              <Field label="Nombre"><Input required minLength={2} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
              <Field label="Cliente"><Input value={form.client_name} onChange={(e) => setForm({ ...form, client_name: e.target.value })} /></Field>
              <Field label="Ubicacion"><Input value={form.location} onChange={(e) => setForm({ ...form, location: e.target.value })} /></Field>
              <Field label="Entrada"><Input type="time" required value={form.start_time} onChange={(e) => setForm({ ...form, start_time: e.target.value })} /></Field>
              <Field label="Salida"><Input type="time" required value={form.end_time} onChange={(e) => setForm({ ...form, end_time: e.target.value })} /></Field>
              <Field label="Tolerancia (minutos)"><Input type="number" min="0" max="120" value={form.tolerance_minutes} onChange={(e) => setForm({ ...form, tolerance_minutes: e.target.value })} /></Field>
              <Field label="Ubicacion: pegar coordenadas de Google Maps (lat, lng)">
                <Input placeholder="-25.2867, -57.6470" value={form.latitude && form.longitude ? `${form.latitude}, ${form.longitude}` : form.latitude}
                  onChange={(e) => {
                    const m = e.target.value.match(/(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)/);
                    setForm({ ...form, latitude: m ? m[1] : e.target.value, longitude: m ? m[2] : "" });
                  }} />
              </Field>
              <Field label="Radio permitido para marcar (metros)"><Input type="number" min="20" max="5000" value={form.geofence_radius_m} onChange={(e) => setForm({ ...form, geofence_radius_m: e.target.value })} /></Field>
              <label className="flex items-center gap-2 pt-5 text-sm"><input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Activa</label>
              <div className="md:col-span-2">
                <p className="mb-1 text-xs font-semibold text-nx-muted">Dias laborables (jornada completa)</p>
                <div className="flex flex-wrap gap-1">
                  {WEEKDAYS.map((w, i) => (
                    <button type="button" key={w} onClick={() => toggleDay(i)}
                      className={`rounded-full px-3 py-1 text-xs font-bold ${form.workdays.includes(i) ? "bg-nx-accent text-[#06352f]" : "border border-nx-line text-nx-muted"}`}>{w}</button>
                  ))}
                </div>
              </div>
              <div className="md:col-span-2">
                <p className="mb-1 text-xs font-semibold text-nx-muted">Dias con horario especial (ej. sabado medio dia; ese dia vale lo que dura)</p>
                <div className="flex flex-wrap gap-1">
                  {WEEKDAYS.map((w, i) => (
                    <button type="button" key={w} onClick={() => toggleSpecial(i)}
                      className={`rounded-full px-3 py-1 text-xs font-bold ${form.special[i] ? "bg-amber-300 text-[#3a2a00]" : "border border-nx-line text-nx-muted"}`}>{w}</button>
                  ))}
                </div>
                {Object.entries(form.special).map(([wd, v]) => (
                  <div key={wd} className="mt-2 grid grid-cols-[3rem_1fr_1fr] items-center gap-2 text-sm">
                    <span>{WEEKDAYS[Number(wd)]}</span>
                    <Input type="time" value={v.start_time} onChange={(e) => setForm({ ...form, special: { ...form.special, [wd]: { ...v, start_time: e.target.value } } })} />
                    <Input type="time" value={v.end_time} onChange={(e) => setForm({ ...form, special: { ...form.special, [wd]: { ...v, end_time: e.target.value } } })} />
                  </div>
                ))}
              </div>
              <div className="flex gap-2 md:col-span-2">
                <SmallButton type="submit" tone="primary" disabled={action.busy}>{form.id ? "Guardar cambios" : "Crear obra"}</SmallButton>
                {form.id && <SmallButton onClick={() => setForm(EMPTY)}>Cancelar</SmallButton>}
              </div>
            </form>
          </Panel>
        )}
      </div>

      {write && (
        <Panel title="Celulares para marcar asistencia" className="mt-5">
          <p className="mb-3 text-xs text-nx-muted">
            Cada obra puede tener un celular o tablet donde la gente marca entrada y salida con su C.I. El link queda atado al primer
            aparato que lo abre; si cambian el aparato, usa &quot;Reiniciar vinculo&quot;. Si la obra tiene coordenadas, solo se puede marcar estando ahi.
          </p>
          <form className="mb-4 grid grid-cols-1 items-end gap-2 md:grid-cols-[2fr_2fr_auto]" onSubmit={async (e) => {
            e.preventDefault();
            let link: string | null = null;
            if (await action.run(async () => {
              const created = await client.post<HrDevice>("/hr/devices", dev);
              link = `${window.location.origin}/marcar/${ctx.system_access_id}/${created.token}`;
            }, "Celular habilitado.")) {
              setNewLink(link);
              setDev({ site_id: "", name: "" });
              devices.reload();
            }
          }}>
            <Field label="Obra">
              <select required className="rounded-xl border border-nx-line bg-white/5 px-3 py-2 text-sm" value={dev.site_id} onChange={(e) => setDev({ ...dev, site_id: e.target.value })}>
                <option value="">Elegir...</option>
                {(sites.data ?? []).filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
              </select>
            </Field>
            <Field label="Nombre del aparato"><Input required minLength={2} placeholder="Tablet porteria" value={dev.name} onChange={(e) => setDev({ ...dev, name: e.target.value })} /></Field>
            <SmallButton type="submit" tone="primary" disabled={action.busy}>Habilitar</SmallButton>
          </form>
          {newLink && (
            <div className="mb-4 rounded-xl border border-amber-300/50 bg-amber-400/10 p-3 text-sm">
              <p className="font-bold">Abri este link en el celular de la obra (se muestra una sola vez):</p>
              <p className="mt-1 break-all font-mono text-xs">{newLink}</p>
              <div className="mt-2 flex gap-2">
                <SmallButton onClick={() => navigator.clipboard?.writeText(newLink)}>Copiar</SmallButton>
                <SmallButton onClick={() => setNewLink(null)}>Listo</SmallButton>
              </div>
            </div>
          )}
          <Table compact rows={devices.data ?? []} rowKey={(d) => d.id} empty="Ningun celular habilitado." columns={[
            { header: "Aparato", cell: (d) => <><p className="font-semibold">{d.name}</p><p className="text-xs text-nx-muted">{siteName(d.site_id)}</p></> },
            { header: "Estado", cell: (d) => (!d.is_active ? "Deshabilitado" : d.bound ? "Vinculado" : "Esperando primer uso") },
            { header: "Ultimo uso", cell: (d) => (d.last_used_at ? day(d.last_used_at) : "-") },
            { header: "", cell: (d) => d.is_active ? (
              <div className="flex justify-end gap-1">
                <SmallButton disabled={action.busy} onClick={async () => { if (await action.run(() => client.post(`/hr/devices/${d.id}/reset`), "Vinculo reiniciado: el proximo aparato que abra el link queda habilitado.")) devices.reload(); }}>Reiniciar vinculo</SmallButton>
                <SmallButton tone="danger" disabled={action.busy} onClick={async () => { if (window.confirm("¿Deshabilitar este celular?") && await action.run(() => client.post(`/hr/devices/${d.id}/disable`), "Celular deshabilitado.")) devices.reload(); }}>Deshabilitar</SmallButton>
              </div>
            ) : null },
          ]} />
        </Panel>
      )}
    </>
  );
}
