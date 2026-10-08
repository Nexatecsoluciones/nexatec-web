"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, label, money, type HrAbsence, type HrAdvance } from "@/lib/erp";
import { addDays, HrTabs, todayISO, useHrRefs } from "../shared";

export default function AdelantosPage() {
  const { client, can } = useErp();
  const amounts = can("hr:amounts");
  const [range, setRange] = useState({ from: addDays(todayISO(), -31), to: todayISO() });
  const [adv, setAdv] = useState({ employee_id: "", advance_date: todayISO(), amount: "", pay_method: "CASH", notes: "" });
  const [abs, setAbs] = useState({ employee_id: "", start_date: todayISO(), end_date: todayISO(), kind: "PERMISSION", notes: "" });
  const action = useAction();
  const refs = useHrRefs();

  const advances = useLoad(() => (amounts
    ? client.get<HrAdvance[]>("/hr/advances", { date_from: range.from, date_to: range.to })
    : Promise.resolve([] as HrAdvance[])), [range.from, range.to]);
  const absences = useLoad(() => client.get<HrAbsence[]>("/hr/absences", { date_from: range.from, date_to: range.to }), [range.from, range.to]);
  const name = (id: string) => refs.data?.employees.find((e) => e.id === id)?.full_name ?? "-";
  const emps = refs.data?.employees ?? [];

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Adelantos de sueldo (se descuentan en la planilla) y ausencias justificadas" />
      <HrTabs />
      {(refs.error || advances.error || absences.error) && <Notice>{refs.error ?? advances.error ?? absences.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      <div className="mb-5 grid grid-cols-2 gap-3 md:w-1/2">
        <Field label="Desde"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
        <Field label="Hasta"><Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {amounts && (
          <Panel title="Adelantos">
            {can("hr:write") && (
              <form className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-2" onSubmit={async (e) => {
                e.preventDefault();
                const body = { ...adv, notes: adv.notes || null };
                if (await action.run(() => client.post("/hr/advances", body, true), "Adelanto registrado.")) {
                  setAdv({ ...adv, amount: "", notes: "" });
                  advances.reload();
                }
              }}>
                <Field label="Empleado">
                  <Select required value={adv.employee_id} onChange={(e) => setAdv({ ...adv, employee_id: e.target.value })}>
                    <option value="">Elegir...</option>
                    {emps.map((x) => <option key={x.id} value={x.id}>{x.full_name}</option>)}
                  </Select>
                </Field>
                <Field label="Fecha"><Input type="date" required max={todayISO()} value={adv.advance_date} onChange={(e) => setAdv({ ...adv, advance_date: e.target.value })} /></Field>
                <Field label="Monto (Gs.)"><Input type="number" required min="1" step="1" value={adv.amount} onChange={(e) => setAdv({ ...adv, amount: e.target.value })} /></Field>
                <Field label="Forma de pago">
                  <Select value={adv.pay_method} onChange={(e) => setAdv({ ...adv, pay_method: e.target.value })}>
                    <option value="CASH">Efectivo</option><option value="TRANSFER">Transferencia</option>
                  </Select>
                </Field>
                <Field label="Observacion"><Input value={adv.notes} onChange={(e) => setAdv({ ...adv, notes: e.target.value })} /></Field>
                <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar adelanto</SmallButton></div>
              </form>
            )}
            <Table compact rows={advances.data ?? []} rowKey={(a) => a.id} empty="Sin adelantos en el rango." columns={[
              { header: "Nro", cell: (a) => <span className={a.voided ? "line-through text-nx-muted" : ""}>{a.number}</span> },
              { header: "Empleado", cell: (a) => a.employee_name },
              { header: "Fecha", cell: (a) => day(a.advance_date) },
              { header: "Monto", cell: (a) => money(a.amount), align: "right" },
              ...(can("hr:payroll") ? [{ header: "", cell: (a: HrAdvance) => (a.voided ? <span className="text-xs text-nx-muted">Anulado</span> : (
                <SmallButton tone="danger" disabled={action.busy} onClick={async () => {
                  const reason = window.prompt("Motivo de la anulacion:");
                  if (reason && await action.run(() => client.post(`/hr/advances/${a.id}/void`, { reason }), "Adelanto anulado.")) advances.reload();
                }}>Anular</SmallButton>
              )) }] : []),
            ]} />
            <p className="mt-2 text-right text-sm font-bold">Total vigente: {money((advances.data ?? []).filter((a) => !a.voided).reduce((s, a) => s + Number(a.amount), 0))}</p>
          </Panel>
        )}

        <Panel title="Ausencias justificadas">
          {can("hr:attendance") && (
            <form className="mb-4 grid grid-cols-1 gap-2 md:grid-cols-2" onSubmit={async (e) => {
              e.preventDefault();
              if (await action.run(() => client.post("/hr/absences", { ...abs, notes: abs.notes || null }), "Ausencia registrada.")) {
                setAbs({ ...abs, notes: "" });
                absences.reload();
              }
            }}>
              <Field label="Empleado">
                <Select required value={abs.employee_id} onChange={(e) => setAbs({ ...abs, employee_id: e.target.value })}>
                  <option value="">Elegir...</option>
                  {emps.map((x) => <option key={x.id} value={x.id}>{x.full_name}</option>)}
                </Select>
              </Field>
              <Field label="Tipo">
                <Select value={abs.kind} onChange={(e) => setAbs({ ...abs, kind: e.target.value })}>
                  {["PERMISSION", "MEDICAL", "VACATION", "OTHER"].map((k) => <option key={k} value={k}>{label(k)}</option>)}
                </Select>
              </Field>
              <Field label="Desde"><Input type="date" required value={abs.start_date} onChange={(e) => setAbs({ ...abs, start_date: e.target.value })} /></Field>
              <Field label="Hasta"><Input type="date" required value={abs.end_date} onChange={(e) => setAbs({ ...abs, end_date: e.target.value })} /></Field>
              <Field label="Observacion"><Input value={abs.notes} onChange={(e) => setAbs({ ...abs, notes: e.target.value })} /></Field>
              <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar ausencia</SmallButton></div>
            </form>
          )}
          <Table compact rows={absences.data ?? []} rowKey={(a) => a.id} empty="Sin ausencias en el rango." columns={[
            { header: "Empleado", cell: (a) => name(a.employee_id) },
            { header: "Tipo", cell: (a) => label(a.kind) },
            { header: "Periodo", cell: (a) => `${day(a.start_date)} al ${day(a.end_date)}` },
          ]} />
        </Panel>
      </div>
    </>
  );
}
