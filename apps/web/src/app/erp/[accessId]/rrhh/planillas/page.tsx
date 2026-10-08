"use client";

import { useEffect, useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Stat, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, quantity, type HrPayroll, type HrPayrollLine } from "@/lib/erp";
import { HrTabs, useHrRefs } from "../shared";

function Lines({ lines }: { lines: HrPayrollLine[] }) {
  return (
    <Table rows={lines} rowKey={(l) => l.employee_id} empty="Sin empleados en el periodo." columns={[
      { header: "Empleado", cell: (l) => <><p className="font-semibold">{l.employee_name}</p><p className="text-xs text-nx-muted">C.I. {l.national_id} · {label(l.pay_method)}</p></> },
      { header: "Dias", cell: (l) => <>{l.days_worked}{l.unexcused_absences > 0 && <span className="text-red-300"> ({l.unexcused_absences} f.)</span>}</>, align: "right" },
      { header: "Horas", cell: (l) => <>{quantity(l.regular_hours)}{Number(l.overtime_hours) > 0 && <span className="text-xs text-nx-muted"> +{quantity(l.overtime_hours)} ext.</span>}</>, align: "right" },
      { header: "Jornal/h", cell: (l) => money(l.hourly_rate), align: "right" },
      { header: "Bruto", cell: (l) => <span title={`Base ${money(l.base_amount)} · Extra ${money(l.overtime_amount)} · Premio ${money(l.bonus_amount)} · Ajuste ${money(l.adjustment)}`}>{money(l.gross)}</span>, align: "right" },
      { header: "IPS 9%", cell: (l) => money(l.ips_employee), align: "right" },
      { header: "Adelantos", cell: (l) => money(l.advances), align: "right" },
      { header: "A cobrar", cell: (l) => <strong>{money(l.net)}</strong>, align: "right" },
    ]} />
  );
}

/** CSV con ; (Excel en español) y BOM para que respete los acentos. */
function downloadCsv(name: string, header: string[], rows: (string | number)[][]) {
  const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  const body = [header, ...rows].map((r) => r.map(esc).join(";")).join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff" + body], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function PlanillasPage() {
  const { client, can, base } = useErp();
  const canPay = can("hr:payroll");
  const [period, setPeriod] = useState({ start: "", end: "", site_id: "" });
  const [selected, setSelected] = useState<string | null>(null);
  const [ov, setOv] = useState({ employee_id: "", force_bonus: false, adjustment: "", reason: "" });
  const action = useAction();
  const refs = useHrRefs();

  const current = useLoad(() => client.get<{ period_start: string; period_end: string }>("/hr/period"), []);
  useEffect(() => {
    if (current.data && !period.start) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPeriod((p) => ({ ...p, start: current.data!.period_start, end: current.data!.period_end }));
    }
  }, [current.data, period.start]);

  const preview = useLoad(() => (period.start && period.end
    ? client.get<HrPayrollLine[]>("/hr/payroll-preview", { period_start: period.start, period_end: period.end, site_id: period.site_id })
    : Promise.resolve([] as HrPayrollLine[])), [period.start, period.end, period.site_id]);
  const list = useLoad(() => client.get<HrPayroll[]>("/hr/payrolls"), []);
  const detail = useLoad(() => (selected ? client.get<HrPayroll>(`/hr/payrolls/${selected}`) : Promise.resolve(null)), [selected]);
  const siteName = (id: string | null) => (id ? refs.data?.sites.find((s) => s.id === id)?.name ?? "-" : "Todas las obras");
  const sum = (rows: HrPayrollLine[], k: keyof HrPayrollLine) => rows.reduce((s, r) => s + Number(r[k]), 0);
  const p = detail.data;
  const rows = preview.data ?? [];

  async function act(fn: () => Promise<unknown>, ok: string) {
    if (await action.run(fn, ok)) { list.reload(); detail.reload(); preview.reload(); }
  }

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Planilla de pagos: horas x jornal + extras + premio -> IPS -> adelantos -> a cobrar" />
      <HrTabs />
      {(preview.error || list.error || detail.error) && <Notice>{preview.error ?? list.error ?? detail.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      <Panel title="Periodo" className="mb-5">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_1fr_2fr_auto]">
          <Field label="Desde"><Input type="date" value={period.start} onChange={(e) => setPeriod({ ...period, start: e.target.value })} /></Field>
          <Field label="Hasta"><Input type="date" value={period.end} onChange={(e) => setPeriod({ ...period, end: e.target.value })} /></Field>
          <Field label="Obra">
            <Select value={period.site_id} onChange={(e) => setPeriod({ ...period, site_id: e.target.value })}>
              <option value="">Todas las obras</option>
              {(refs.data?.sites ?? []).map((s) => <option key={s.id} value={s.id}>{s.code} - {s.name}</option>)}
            </Select>
          </Field>
          {canPay && <div className="flex items-end"><SmallButton tone="primary" disabled={action.busy || !rows.length} onClick={() => act(async () => {
            const created = await client.post<HrPayroll>("/hr/payrolls", { period_start: period.start, period_end: period.end, site_id: period.site_id || null });
            setSelected(created.id);
          }, "Planilla creada en borrador.")}>Crear planilla</SmallButton></div>}
        </div>
      </Panel>

      {p ? (
        <Panel title={`Planilla ${p.number} · ${day(p.period_start)} al ${day(p.period_end)} · ${siteName(p.site_id)}`} className="mb-5">
          <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-5">
            <Stat label="Estado" value={p.status === "CLOSED" ? "Cerrada" : "Borrador"} />
            <Stat label="Bruto" value={money(p.total_gross)} />
            <Stat label="IPS (9% / 16,5%)" value={`${money(p.total_ips_employee)} / ${money(p.total_ips_employer)}`} />
            <Stat label="Adelantos" value={money(p.total_advances)} />
            <Stat label="Total a pagar" value={money(p.total_net)} />
          </div>
          <Lines lines={p.lines} />
          <div className="mt-4 flex flex-wrap justify-end gap-2">
            <a className="rounded-full border border-nx-line px-4 py-1.5 text-sm font-semibold hover:border-nx-accent" target="_blank" rel="noopener noreferrer"
              href={`${base.replace("/erp/", "/imprimir/")}/recibos/${p.id}`}>Recibos para imprimir</a>
            <SmallButton onClick={() => downloadCsv(`IPS-${p.number}.csv`,
              ["C.I.", "Apellidos y nombres", "Dias trabajados", "Salario imponible", "Aporte obrero", "Aporte patronal"],
              p.lines.filter((l) => Number(l.ips_employee) > 0).map((l) => [l.national_id, l.employee_name, l.days_worked,
                Number(l.gross), Number(l.ips_employee), Number(l.ips_employer)]))}>Planilla IPS (CSV)</SmallButton>
            <SmallButton onClick={() => downloadCsv(`Transferencias-${p.number}.csv`, ["C.I.", "Nombre", "Cuenta", "Monto"],
              p.lines.filter((l) => l.pay_method === "TRANSFER" && Number(l.net) > 0).map((l) => [l.national_id, l.employee_name,
                l.bank_account ?? "", Number(l.net)]))}>Pagos por transferencia (CSV)</SmallButton>
            <SmallButton onClick={() => setSelected(null)}>Volver a la vista previa</SmallButton>
            {p.status === "DRAFT" && canPay && <>
              <SmallButton disabled={action.busy} onClick={() => act(() => client.post(`/hr/payrolls/${p.id}/recalculate`), "Planilla recalculada.")}>Recalcular</SmallButton>
              <SmallButton tone="danger" disabled={action.busy} onClick={() => window.confirm("¿Borrar este borrador?") && act(async () => { await client.del(`/hr/payrolls/${p.id}`); setSelected(null); }, "Borrador eliminado.")}>Borrar borrador</SmallButton>
              <SmallButton tone="primary" disabled={action.busy} onClick={() => window.confirm("Al cerrar la planilla, sus montos quedan fijos y el periodo ya no se puede modificar. ¿Cerrar?") && act(() => client.post(`/hr/payrolls/${p.id}/close`), "Planilla cerrada.")}>Cerrar planilla</SmallButton>
            </>}
          </div>
        </Panel>
      ) : (
        <Panel title="Vista previa (todavia no guardada)" className="mb-5">
          {rows.length > 0 && (
            <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-4">
              <Stat label="Empleados" value={String(rows.length)} />
              <Stat label="Bruto" value={money(sum(rows, "gross"))} />
              <Stat label="Adelantos" value={money(sum(rows, "advances"))} />
              <Stat label="Total a pagar" value={money(sum(rows, "net"))} />
            </div>
          )}
          <Lines lines={rows} />
          {canPay && rows.length > 0 && (
            <form className="mt-4 grid grid-cols-1 items-end gap-2 rounded-xl border border-nx-line p-3 md:grid-cols-[2fr_auto_1fr_2fr_auto]" onSubmit={(e) => {
              e.preventDefault();
              act(() => client.put("/hr/payroll-overrides", { employee_id: ov.employee_id, period_start: period.start, period_end: period.end,
                force_bonus: ov.force_bonus, adjustment: ov.adjustment || "0", reason: ov.reason || null }), "Excepcion guardada.");
            }}>
              <Field label="Excepcion para">
                <Select required value={ov.employee_id} onChange={(e) => setOv({ ...ov, employee_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {rows.map((r) => <option key={r.employee_id} value={r.employee_id}>{r.employee_name}</option>)}
                </Select>
              </Field>
              <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={ov.force_bonus} onChange={(e) => setOv({ ...ov, force_bonus: e.target.checked })} /> Pagar premio igual</label>
              <Field label="Ajuste (+/- Gs.)"><Input type="number" step="1" value={ov.adjustment} onChange={(e) => setOv({ ...ov, adjustment: e.target.value })} /></Field>
              <Field label="Motivo"><Input value={ov.reason} onChange={(e) => setOv({ ...ov, reason: e.target.value })} /></Field>
              <SmallButton type="submit" disabled={action.busy}>Guardar</SmallButton>
            </form>
          )}
        </Panel>
      )}

      <Panel title="Planillas guardadas">
        <Table compact rows={list.data ?? []} rowKey={(r) => r.id} empty="Todavia no hay planillas." columns={[
          { header: "Nro", cell: (r) => <button className="text-nx-accent hover:underline" onClick={() => setSelected(r.id)}>{r.number}</button> },
          { header: "Periodo", cell: (r) => `${day(r.period_start)} al ${day(r.period_end)}` },
          { header: "Obra", cell: (r) => siteName(r.site_id) },
          { header: "Estado", cell: (r) => (r.status === "CLOSED" ? "Cerrada" : "Borrador") },
          { header: "A pagar", cell: (r) => money(r.total_net), align: "right" },
        ]} />
      </Panel>
    </>
  );
}
