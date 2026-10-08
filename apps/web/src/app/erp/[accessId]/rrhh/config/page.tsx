"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, money, type HrCategory, type HrSettings } from "@/lib/erp";
import { HrTabs } from "../shared";

interface Block { id: string; national_id: string; full_name: string | null; reason: string; included_on: string }

export default function ConfigPage() {
  const { client, can } = useErp();
  const [s, setS] = useState<HrSettings | null>(null);
  const [cat, setCat] = useState({ code: "", default_trade: "", hourly_rate: "", manual_rate: false });
  const [blk, setBlk] = useState({ national_id: "", full_name: "", reason: "" });
  const action = useAction();
  const settings = useLoad(async () => { const r = await client.get<HrSettings>("/hr/settings"); setS(r); return r; }, []);
  const cats = useLoad(() => client.get<HrCategory[]>("/hr/categories"), []);
  const blocks = useLoad(() => (can("hr:write") ? client.get<Block[]>("/hr/blocklist") : Promise.resolve([] as Block[])), []);

  return (
    <>
      <PageTitle title="RR.HH." subtitle="Reglas de calculo, categorias y personas bloqueadas" />
      <HrTabs />
      {(settings.error || cats.error) && <Notice>{settings.error ?? cats.error}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        {s && (
          <Panel title="Reglas de la empresa">
            <form className="grid grid-cols-1 gap-3 md:grid-cols-2" onSubmit={async (e) => {
              e.preventDefault();
              if (await action.run(() => client.put("/hr/settings", s), "Reglas guardadas.")) settings.reload();
            }}>
              <Field label="Periodo de pago">
                <Select value={s.pay_period} onChange={(e) => setS({ ...s, pay_period: e.target.value as HrSettings["pay_period"] })}>
                  <option value="BIWEEKLY">Quincenal (1-15 y 16-fin)</option><option value="MONTHLY">Mensual</option>
                </Select>
              </Field>
              <Field label="Horas que vale un dia normal"><Input type="number" min="1" max="24" step="0.5" value={s.workday_hours} onChange={(e) => setS({ ...s, workday_hours: e.target.value })} /></Field>
              <Field label="Redondeo de tardanzas y extras">
                <Select value={String(s.rounding_minutes)} onChange={(e) => setS({ ...s, rounding_minutes: Number(e.target.value) })}>
                  {[1, 5, 10, 15, 30].map((m) => <option key={m} value={m}>{m} minutos</option>)}
                </Select>
              </Field>
              <Field label="Recargo hora extra (1,5 = +50%)"><Input type="number" min="1" max="3" step="0.05" value={s.overtime_multiplier} onChange={(e) => setS({ ...s, overtime_multiplier: e.target.value })} /></Field>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.deduct_ips} onChange={(e) => setS({ ...s, deduct_ips: e.target.checked })} /> Calcular IPS</label>
              <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={s.attendance_bonus_enabled} onChange={(e) => setS({ ...s, attendance_bonus_enabled: e.target.checked })} /> Premio por asistencia perfecta</label>
              <Field label="IPS trabajador (%)"><Input type="number" min="0" max="100" step="0.01" value={s.ips_employee_pct} onChange={(e) => setS({ ...s, ips_employee_pct: e.target.value })} /></Field>
              <Field label="IPS empleador (%)"><Input type="number" min="0" max="100" step="0.01" value={s.ips_employer_pct} onChange={(e) => setS({ ...s, ips_employer_pct: e.target.value })} /></Field>
              <p className="text-xs text-nx-muted md:col-span-2">El IPS se calcula solo para quien tiene fecha de entrada al IPS. El premio se paga por hora trabajada y se pierde con una falta injustificada en el periodo (salvo excepcion en la planilla).</p>
              <div><SmallButton type="submit" tone="primary" disabled={action.busy}>Guardar reglas</SmallButton></div>
            </form>
          </Panel>
        )}

        <Panel title="Categorias">
          <form className="mb-4 grid grid-cols-1 items-end gap-2 md:grid-cols-[1fr_1fr_1fr_auto]" onSubmit={async (e) => {
            e.preventDefault();
            const body = { ...cat, default_trade: cat.default_trade || null, hourly_rate: cat.hourly_rate || "0" };
            if (await action.run(() => client.post("/hr/categories", body), "Categoria creada.")) {
              setCat({ code: "", default_trade: "", hourly_rate: "", manual_rate: false });
              cats.reload();
            }
          }}>
            <Field label="Codigo"><Input required minLength={2} placeholder="OFICIAL" value={cat.code} onChange={(e) => setCat({ ...cat, code: e.target.value })} /></Field>
            <Field label="Oficio por defecto"><Input value={cat.default_trade} onChange={(e) => setCat({ ...cat, default_trade: e.target.value })} /></Field>
            <Field label="Jornal/hora (Gs.)"><Input type="number" min="0" step="1" value={cat.hourly_rate} disabled={cat.manual_rate} onChange={(e) => setCat({ ...cat, hourly_rate: e.target.value })} /></Field>
            <SmallButton type="submit" tone="primary" disabled={action.busy}>Agregar</SmallButton>
            <label className="flex items-center gap-2 text-xs md:col-span-4"><input type="checkbox" checked={cat.manual_rate} onChange={(e) => setCat({ ...cat, manual_rate: e.target.checked })} /> Sueldo individual (no se autocompleta)</label>
          </form>
          <Table compact rows={cats.data ?? []} rowKey={(c) => c.id} empty="Sin categorias." columns={[
            { header: "Categoria", cell: (c) => c.code },
            { header: "Oficio", cell: (c) => c.default_trade ?? "-" },
            { header: "Jornal/h", cell: (c) => (c.manual_rate ? "Individual" : money(c.hourly_rate)), align: "right" },
          ]} />
        </Panel>

        {can("hr:write") && (
          <Panel title="Personas bloqueadas">
            <p className="mb-3 text-xs text-nx-muted">Una C.I. bloqueada no se puede dar de alta ni reactivar. Si la persona esta activa, se la da de baja y se cierra su IPS.</p>
            <form className="mb-4 grid grid-cols-1 items-end gap-2 md:grid-cols-[1fr_1.5fr_2fr_auto]" onSubmit={async (e) => {
              e.preventDefault();
              if (window.confirm("¿Bloquear esa C.I.? Si esta activa se da de baja.") &&
                  await action.run(() => client.post("/hr/blocklist", { ...blk, full_name: blk.full_name || null }), "C.I. bloqueada.")) {
                setBlk({ national_id: "", full_name: "", reason: "" });
                blocks.reload();
              }
            }}>
              <Field label="C.I."><Input required minLength={5} value={blk.national_id} onChange={(e) => setBlk({ ...blk, national_id: e.target.value })} /></Field>
              <Field label="Nombre"><Input value={blk.full_name} onChange={(e) => setBlk({ ...blk, full_name: e.target.value })} /></Field>
              <Field label="Motivo"><Input required minLength={3} value={blk.reason} onChange={(e) => setBlk({ ...blk, reason: e.target.value })} /></Field>
              <SmallButton type="submit" tone="danger" disabled={action.busy}>Bloquear</SmallButton>
            </form>
            <Table compact rows={blocks.data ?? []} rowKey={(b) => b.id} empty="Nadie bloqueado." columns={[
              { header: "C.I.", cell: (b) => b.national_id },
              { header: "Nombre", cell: (b) => b.full_name ?? "-" },
              { header: "Motivo", cell: (b) => b.reason },
              { header: "Desde", cell: (b) => day(b.included_on) },
            ]} />
          </Panel>
        )}
      </div>
    </>
  );
}
