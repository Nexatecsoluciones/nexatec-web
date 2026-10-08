"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, hhmm, label, type HrAttendance } from "@/lib/erp";
import { addDays, HrTabs, todayISO, useHrRefs } from "../shared";

type Row = { time_in: string; time_out: string; notes: string };

export default function AsistenciaPage() {
  const { client, can } = useErp();
  const canMark = can("hr:attendance");
  const canFix = can("hr:payroll");
  const [siteId, setSiteId] = useState("");
  const [workDate, setWorkDate] = useState(todayISO());
  const [draft, setDraft] = useState<Record<string, Row>>({});
  const action = useAction();
  const refs = useHrRefs();

  const period = useLoad(() => client.get<{ period_start: string; period_end: string }>("/hr/period", { day: workDate }), [workDate]);
  const dayRows = useLoad(() => (siteId
    ? client.get<HrAttendance[]>("/hr/attendance", { date_from: workDate, date_to: workDate, site_id: siteId })
    : Promise.resolve([] as HrAttendance[])), [siteId, workDate]);
  const grid = useLoad(() => (siteId && period.data
    ? client.get<HrAttendance[]>("/hr/attendance", { date_from: period.data.period_start, date_to: period.data.period_end, site_id: siteId })
    : Promise.resolve([] as HrAttendance[])), [siteId, period.data?.period_start]);
  const pending = useLoad(() => client.get<HrAttendance[]>("/hr/attendance", {
    date_from: addDays(todayISO(), -62), date_to: todayISO(), overtime: "PENDING" }), []);

  const site = refs.data?.sites.find((s) => s.id === siteId);
  const people = (refs.data?.employees ?? []).filter((e) => e.current_site_id === siteId && e.tracks_attendance);
  const existing = (empId: string) => dayRows.data?.find((a) => a.employee_id === empId);
  const reloadAll = () => { dayRows.reload(); grid.reload(); pending.reload(); };

  function row(empId: string): Row {
    const a = existing(empId);
    return draft[empId] ?? { time_in: a?.time_in?.slice(0, 5) ?? "", time_out: a?.time_out?.slice(0, 5) ?? "", notes: a?.notes ?? "" };
  }

  async function save(empId: string) {
    const r = row(empId);
    const body = { employee_id: empId, site_id: siteId, work_date: workDate, time_in: r.time_in || null,
      time_out: r.time_out || null, notes: r.notes || null };
    if (await action.run(() => client.post("/hr/attendance", body), "Asistencia guardada.")) {
      setDraft((d) => { const n = { ...d }; delete n[empId]; return n; });
      reloadAll();
    }
  }

  const days: string[] = [];
  if (period.data) for (let d = period.data.period_start; d <= period.data.period_end; d = addDays(d, 1)) days.push(d);
  const cell = (empId: string, d: string) => grid.data?.find((a) => a.employee_id === empId && a.work_date === d);

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Asistencia por obra y dia; horas extra con aprobacion" />
      <HrTabs />
      {(refs.error || dayRows.error || grid.error) && <Notice>{refs.error ?? dayRows.error ?? grid.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-[2fr_1fr]">
        <Field label="Obra">
          <Select value={siteId} onChange={(e) => { setSiteId(e.target.value); setDraft({}); }}>
            <option value="">Elegir obra...</option>
            {(refs.data?.sites ?? []).filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
          </Select>
        </Field>
        <Field label="Dia"><Input type="date" max={todayISO()} value={workDate} onChange={(e) => { setWorkDate(e.target.value); setDraft({}); }} /></Field>
      </div>

      {site && (
        <Panel title={`Planilla del ${day(workDate)} · horario ${hhmm(site.start_time)} a ${hhmm(site.end_time)}, tolerancia ${site.tolerance_minutes} min`} className="mb-5">
          <Table compact rows={people} rowKey={(p) => p.id} empty="No hay personal asignado a esta obra." columns={[
            { header: "Empleado", cell: (p) => <><p className="font-semibold">{p.full_name}</p><p className="text-xs text-nx-muted">{p.trade ?? ""}</p></> },
            { header: "Entrada", cell: (p) => <Input type="time" className="w-28" disabled={!canMark} value={row(p.id).time_in}
                onChange={(e) => setDraft({ ...draft, [p.id]: { ...row(p.id), time_in: e.target.value } })} /> },
            { header: "Salida", cell: (p) => <Input type="time" className="w-28" disabled={!canMark} value={row(p.id).time_out}
                onChange={(e) => setDraft({ ...draft, [p.id]: { ...row(p.id), time_out: e.target.value } })} /> },
            { header: "Horas", cell: (p) => {
              const a = existing(p.id);
              if (!a) return <span className="text-nx-muted">-</span>;
              return <><p>{a.paid_hours} h{a.manual_override && " (a mano)"}</p>
                {Number(a.overtime_hours) > 0 && <p className="text-xs text-nx-muted">extra {a.overtime_hours} h · {label(a.overtime_status)}</p>}</>;
            } },
            ...(canMark ? [{ header: "", cell: (p: { id: string }) => (
              <SmallButton tone={draft[p.id] ? "primary" : "default"} disabled={action.busy || !draft[p.id]} onClick={() => save(p.id)}>Guardar</SmallButton>
            ) }] : []),
          ]} />
          {canFix && (dayRows.data ?? []).length > 0 && (
            <p className="mt-3 text-xs text-nx-muted">Para corregir a mano las horas de un dia (por ejemplo lluvia), usa &quot;Corregir&quot; en la grilla del periodo.</p>
          )}
        </Panel>
      )}

      {site && period.data && (
        <Panel title={`Horas pagas del periodo ${day(period.data.period_start)} al ${day(period.data.period_end)}`} className="mb-5">
          <div className="overflow-x-auto">
            <table className="text-xs">
              <thead>
                <tr className="text-nx-muted">
                  <th className="sticky left-0 bg-nx-card px-2 py-1 text-left">Empleado</th>
                  {days.map((d) => <th key={d} className="px-1 py-1 font-normal">{d.slice(8)}</th>)}
                  <th className="px-2 py-1">Total</th>
                </tr>
              </thead>
              <tbody>
                {people.map((p) => {
                  const total = days.reduce((acc, d) => acc + Number(cell(p.id, d)?.paid_hours ?? 0), 0);
                  return (
                    <tr key={p.id} className="border-t border-nx-line/40">
                      <td className="sticky left-0 whitespace-nowrap bg-nx-card px-2 py-1">{p.full_name}</td>
                      {days.map((d) => {
                        const a = cell(p.id, d);
                        const pend = a?.overtime_status === "PENDING";
                        return (
                          <td key={d} className="px-1 py-1 text-center">
                            <button title={a ? `${hhmm(a.time_in)}-${hhmm(a.time_out)}` : "Sin registro"}
                              className={`min-w-[32px] rounded px-1 ${a ? (pend ? "bg-amber-400/20 text-amber-200" : a.manual_override ? "bg-sky-400/20" : "bg-white/5") : "text-nx-muted"}`}
                              onClick={async () => {
                                setWorkDate(d);
                                if (a && canFix) {
                                  const h = window.prompt(`Corregir horas pagas del ${day(d)} (vacio = recalcular desde las marcas):`, a.paid_hours);
                                  if (h === null) return;
                                  if (h.trim() === "") {
                                    if (await action.run(() => client.post(`/hr/attendance/${a.id}/reset`), "Horas recalculadas.")) reloadAll();
                                  } else {
                                    const reason = window.prompt("Motivo de la correccion:");
                                    if (reason && await action.run(() => client.post(`/hr/attendance/${a.id}/override`, { paid_hours: h, reason }), "Horas corregidas.")) reloadAll();
                                  }
                                }
                              }}>
                              {a ? Number(a.paid_hours).toString() : "·"}
                            </button>
                          </td>
                        );
                      })}
                      <td className="px-2 py-1 text-right font-bold">{total.toFixed(2)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="mt-2 text-xs text-nx-muted">Amarillo: extra pendiente de aprobar. Celeste: horas corregidas a mano. Click en un dia: lo abre arriba{canFix ? " y permite corregir las horas" : ""}.</p>
        </Panel>
      )}

      <Panel title={`Horas extra pendientes (${pending.data?.length ?? 0})`}>
        <Table compact rows={pending.data ?? []} rowKey={(a) => a.id} empty="No hay horas extra pendientes." columns={[
          { header: "Empleado", cell: (a) => a.employee_name },
          { header: "Dia", cell: (a) => day(a.work_date) },
          { header: "Marcas", cell: (a) => `${hhmm(a.time_in)} - ${hhmm(a.time_out)}` },
          { header: "Extra", cell: (a) => `${a.overtime_hours} h`, align: "right" },
          ...(canMark ? [{ header: "", cell: (a: HrAttendance) => (
            <div className="flex justify-end gap-1">
              <SmallButton tone="primary" disabled={action.busy} onClick={async () => { if (await action.run(() => client.post(`/hr/attendance/${a.id}/overtime`, { approve: true }), "Extra aprobada.")) reloadAll(); }}>Aprobar</SmallButton>
              <SmallButton tone="danger" disabled={action.busy} onClick={async () => { if (await action.run(() => client.post(`/hr/attendance/${a.id}/overtime`, { approve: false }), "Extra rechazada.")) reloadAll(); }}>Rechazar</SmallButton>
            </div>
          ) }] : []),
        ]} />
      </Panel>
    </>
  );
}
