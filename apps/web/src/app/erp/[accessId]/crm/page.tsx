"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Stat, Table, useAction, useErp, useLoad } from "@/components/erp";
import {
  day, label, money, OPEN_STAGES,
  type Activity, type ActivityKind, type Lead, type Opportunity, type Page, type Party, type Pipeline,
} from "@/lib/erp";

const KINDS: ActivityKind[] = ["CALL", "WHATSAPP", "MEETING", "EMAIL", "TASK", "NOTE"];
const EMPTY_LEAD = { contact_name: "", company_name: "", phone: "", email: "", source: "" };
const EMPTY_OPP = { title: "", account: "", amount: "", expected_close_date: "" };

function when(iso: string | null): string {
  if (!iso) return "Sin fecha";
  return new Date(iso).toLocaleString("es-PY", { timeZone: "America/Asuncion", dateStyle: "short", timeStyle: "short" });
}

/** Quita claves vacias: la API distingue "no mandado" de "vacio". */
function clean<T extends Record<string, string>>(o: T): Partial<T> {
  return Object.fromEntries(Object.entries(o).filter(([, v]) => v.trim() !== "")) as Partial<T>;
}

export default function CrmPage() {
  const { client, can } = useErp();
  const write = can("crm:write");
  const [mine, setMine] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [leadForm, setLeadForm] = useState(EMPTY_LEAD);
  const [oppForm, setOppForm] = useState(EMPTY_OPP);
  const [actForm, setActForm] = useState({ kind: "CALL" as ActivityKind, subject: "", due_at: "" });
  const action = useAction();

  const pipe = useLoad(() => client.get<Pipeline>("/crm/pipeline", { mine }), [mine]);
  const opps = useLoad(() => client.get<Page<Opportunity>>("/crm/opportunities", { open_only: true, limit: 200 }), []);
  const leads = useLoad(() => client.get<Page<Lead>>("/crm/leads", { status: "OPEN", limit: 200 }), []);
  // La hora de referencia para "vencida" se toma al cargar (no en el render).
  const agenda = useLoad(async () => ({
    items: (await client.get<Page<Activity>>("/crm/activities", { pending: true, mine, limit: 50 })).items, at: Date.now(),
  }), [mine]);
  const customers = useLoad(async () => can("parties:read")
    ? (await client.get<Page<Party>>("/parties", { role: "customer", limit: 200 })).items.filter((p) => p.is_active)
    : [], []);
  const detail = useLoad(async () => {
    if (!selected) return null;
    const [opp, acts] = await Promise.all([
      client.get<Opportunity>(`/crm/opportunities/${selected}`),
      client.get<Page<Activity>>("/crm/activities", { opportunity_id: selected, limit: 50 }),
    ]);
    return { opp, acts: acts.items };
  }, [selected]);

  const reloadAll = () => { pipe.reload(); opps.reload(); leads.reload(); agenda.reload(); detail.reload(); };
  const run = async (fn: () => Promise<unknown>, ok: string) => { if (await action.run(fn, ok)) reloadAll(); };

  async function createLead(e: React.FormEvent) {
    e.preventDefault();
    if (await action.run(() => client.post("/crm/leads", clean(leadForm)), "Prospecto cargado.")) {
      setLeadForm(EMPTY_LEAD);
      leads.reload();
    }
  }

  async function createOpp(e: React.FormEvent) {
    e.preventDefault();
    const [kind, id] = oppForm.account.split(":");
    let created: Opportunity | null = null;
    const ok = await action.run(async () => {
      created = await client.post<Opportunity>("/crm/opportunities", {
        title: oppForm.title, amount: oppForm.amount || "0", expected_close_date: oppForm.expected_close_date || null,
        [kind === "lead" ? "lead_id" : "party_id"]: id,
      });
    }, "Oportunidad creada.");
    if (ok && created) {
      setOppForm(EMPTY_OPP);
      setSelected((created as Opportunity).id);
      reloadAll();
    }
  }

  async function addActivity(e: React.FormEvent) {
    e.preventDefault();
    if (!selected) return;
    const body = { kind: actForm.kind, subject: actForm.subject, opportunity_id: selected,
      due_at: actForm.due_at ? new Date(actForm.due_at).toISOString() : null };
    if (await action.run(() => client.post("/crm/activities", body), "Actividad agendada.")) {
      setActForm({ ...actForm, subject: "", due_at: "" });
      detail.reload();
      agenda.reload();
      pipe.reload();
    }
  }

  const p = pipe.data;
  const d = detail.data;
  const now = agenda.data?.at ?? 0;
  const byStage = (stage: string) => (opps.data?.items ?? []).filter((o) => o.stage === stage);
  const stageIdx = d ? OPEN_STAGES.indexOf(d.opp.stage) : -1;
  const err = pipe.error ?? opps.error ?? leads.error ?? agenda.error ?? detail.error;

  return (
    <>
      <PageTitle title="CRM" subtitle="Prospectos -> oportunidades por etapa -> ganada / perdida, con agenda de seguimiento"
        actions={<label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Solo lo mio</label>} />
      {err && <Notice>{err}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {p && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Oportunidades abiertas" value={String(p.open_count)} />
          <Stat label="Monto en juego" value={money(p.open_amount)} />
          <Stat label="Pronostico" value={money(p.forecast)} hint="Suma de monto x probabilidad de las abiertas" />
          <Stat label="Ganado" value={money(p.won_amount)} />
          <Stat label="Tasa de cierre" value={p.win_rate === null ? "-" : `${p.win_rate}%`} hint="Ganadas / (ganadas + perdidas)" />
        </div>
      )}

      <div className="mb-5 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {OPEN_STAGES.map((stage) => {
          const s = p?.stages.find((x) => x.stage === stage);
          return (
            <div key={stage} className="flex min-h-[160px] flex-col gap-2 rounded-2xl border border-nx-line bg-black/10 p-3">
              <div className="flex items-baseline justify-between">
                <p className="font-bold">{label(stage)}</p>
                <p className="text-xs text-nx-muted">{s?.count ?? 0} · {money(s?.amount ?? 0)}</p>
              </div>
              {byStage(stage).map((o) => (
                <button key={o.id} onClick={() => setSelected(o.id)}
                  className={`rounded-xl border p-3 text-left text-sm transition hover:border-nx-accent ${selected === o.id ? "border-nx-accent bg-nx-accent/10" : "border-nx-line bg-nx-card"}`}>
                  <p className="font-semibold">{o.title}</p>
                  <p className="text-xs text-nx-muted">{o.account_name}{o.lead_id && " (prospecto)"}</p>
                  <p className="mt-1 flex justify-between text-xs"><span>{money(o.amount)}</span><span className="text-nx-muted">{o.probability}% · {day(o.expected_close_date)}</span></p>
                </button>
              ))}
              {byStage(stage).length === 0 && <p className="text-xs text-nx-muted">Vacio</p>}
            </div>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <Panel title={d ? `${d.opp.number} · ${d.opp.title}` : "Oportunidad"}>
          {!d ? <p className="text-sm text-nx-muted">Elegir una tarjeta del embudo.</p> : (
            <div className="flex flex-col gap-3 text-sm">
              <p className="text-nx-muted">
                {d.opp.account_name}{d.opp.lead_id && " (prospecto)"} · <strong className="text-nx-text">{label(d.opp.stage)}</strong> · {money(d.opp.amount)} · {d.opp.probability}% · cierre {day(d.opp.expected_close_date)}
              </p>
              {d.opp.lost_reason && <p>Motivo de perdida: {d.opp.lost_reason}</p>}
              {write && stageIdx >= 0 && (
                <div className="flex flex-wrap gap-2">
                  {stageIdx > 0 && <SmallButton disabled={action.busy} onClick={() => run(() => client.post(`/crm/opportunities/${d.opp.id}/stage`, { stage: OPEN_STAGES[stageIdx - 1] }), "Etapa actualizada.")}>&larr; {label(OPEN_STAGES[stageIdx - 1])}</SmallButton>}
                  {stageIdx < OPEN_STAGES.length - 1 && <SmallButton tone="primary" disabled={action.busy} onClick={() => run(() => client.post(`/crm/opportunities/${d.opp.id}/stage`, { stage: OPEN_STAGES[stageIdx + 1] }), "Etapa actualizada.")}>{label(OPEN_STAGES[stageIdx + 1])} &rarr;</SmallButton>}
                  <SmallButton disabled={action.busy} onClick={() => {
                    const amount = window.prompt("Monto (Gs.):", String(Number(d.opp.amount)));
                    if (amount !== null) run(() => client.patch(`/crm/opportunities/${d.opp.id}`, { amount }), "Monto actualizado.");
                  }}>Cambiar monto</SmallButton>
                  <SmallButton tone="primary" disabled={action.busy || !!d.opp.lead_id} onClick={() => run(() => client.post(`/crm/opportunities/${d.opp.id}/win`), "Oportunidad ganada.")}>Ganada</SmallButton>
                  <SmallButton tone="danger" disabled={action.busy} onClick={() => {
                    const reason = window.prompt("Motivo de la perdida (precio, competencia, sin respuesta...):");
                    if (reason) run(() => client.post(`/crm/opportunities/${d.opp.id}/lose`, { reason }), "Oportunidad marcada como perdida.");
                  }}>Perdida</SmallButton>
                </div>
              )}
              {d.opp.lead_id && stageIdx >= 0 && <p className="text-xs text-nx-muted">Para marcarla ganada, primero converti el prospecto en cliente (panel Prospectos).</p>}

              <p className="mt-2 font-bold">Actividades</p>
              <Table compact rows={d.acts} rowKey={(a) => a.id} empty="Sin actividades." columns={[
                { header: "Actividad", cell: (a) => <><p>{a.subject}</p><p className="text-xs text-nx-muted">{label(a.kind)} · {when(a.due_at)}</p></> },
                { header: "Estado", cell: (a) => (a.done_at ? "Hecha" : "Pendiente") },
              ]} />
              {write && stageIdx >= 0 && (
                <form onSubmit={addActivity} className="grid grid-cols-1 gap-2 md:grid-cols-[auto_2fr_1fr_auto]">
                  <Select value={actForm.kind} onChange={(e) => setActForm({ ...actForm, kind: e.target.value as ActivityKind })}>
                    {KINDS.map((k) => <option key={k} value={k}>{label(k)}</option>)}
                  </Select>
                  <Input required minLength={2} placeholder="Asunto" value={actForm.subject} onChange={(e) => setActForm({ ...actForm, subject: e.target.value })} />
                  <Input type="datetime-local" value={actForm.due_at} onChange={(e) => setActForm({ ...actForm, due_at: e.target.value })} />
                  <SmallButton type="submit" tone="primary" disabled={action.busy}>Agendar</SmallButton>
                </form>
              )}
            </div>
          )}
        </Panel>

        <Panel title="Agenda pendiente">
          <Table compact rows={agenda.data?.items ?? []} rowKey={(a) => a.id} empty="Nada pendiente." columns={[
            { header: "Actividad", cell: (a) => (
              <>
                {a.opportunity_id
                  ? <button className="text-left text-nx-accent hover:underline" onClick={() => setSelected(a.opportunity_id)}>{a.subject}</button>
                  : <p>{a.subject}</p>}
                <p className={`text-xs ${a.due_at && Date.parse(a.due_at) < now ? "font-bold text-red-300" : "text-nx-muted"}`}>{label(a.kind)} · {when(a.due_at)}</p>
              </>
            ) },
            ...(write ? [{ header: "", cell: (a: Activity) => <SmallButton disabled={action.busy} onClick={() => {
              const notes = window.prompt("Resultado (opcional):") ?? undefined;
              run(() => client.post(`/crm/activities/${a.id}/complete`, { notes }), "Actividad completada.");
            }}>Hecha</SmallButton> }] : []),
          ]} />
          {(p?.overdue_activities ?? 0) > 0 && <p className="mt-2 text-xs text-red-300">{p?.overdue_activities} vencida(s).</p>}
        </Panel>
      </div>

      <div className="mt-5 grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Prospectos abiertos">
          <Table compact rows={leads.data?.items ?? []} rowKey={(l) => l.id} empty="Sin prospectos abiertos." columns={[
            { header: "Contacto", cell: (l) => <><p className="font-semibold">{l.contact_name}</p><p className="text-xs text-nx-muted">{[l.company_name, l.source].filter(Boolean).join(" · ")}</p></> },
            { header: "Telefono / email", cell: (l) => <><p className="whitespace-nowrap">{l.phone}</p><p className="break-all text-xs text-nx-muted">{l.email}</p></> },
            ...(write ? [{ header: "", cell: (l: Lead) => (
              <div className="flex justify-end gap-1">
                {can("parties:write") && <SmallButton disabled={action.busy} onClick={() => {
                  if (window.confirm(`Convertir a "${l.company_name ?? l.contact_name}" en cliente?`))
                    run(() => client.post(`/crm/leads/${l.id}/convert`), "Prospecto convertido en cliente (completa su RUC en Clientes y proveedores).");
                }}>A cliente</SmallButton>}
                <SmallButton tone="danger" disabled={action.busy} onClick={() => {
                  const reason = window.prompt("Motivo para descartar:");
                  if (reason) run(() => client.post(`/crm/leads/${l.id}/discard`, { reason }), "Prospecto descartado.");
                }}>Descartar</SmallButton>
              </div>
            ) }] : []),
          ]} />
        </Panel>

        {write && (
          <div className="flex flex-col gap-5">
            <Panel title="Nuevo prospecto">
              <form onSubmit={createLead} className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="Contacto"><Input required minLength={2} value={leadForm.contact_name} onChange={(e) => setLeadForm({ ...leadForm, contact_name: e.target.value })} /></Field>
                <Field label="Empresa"><Input value={leadForm.company_name} onChange={(e) => setLeadForm({ ...leadForm, company_name: e.target.value })} /></Field>
                <Field label="Telefono"><Input value={leadForm.phone} onChange={(e) => setLeadForm({ ...leadForm, phone: e.target.value })} /></Field>
                <Field label="Email"><Input type="email" value={leadForm.email} onChange={(e) => setLeadForm({ ...leadForm, email: e.target.value })} /></Field>
                <Field label="Origen"><Input placeholder="WhatsApp, referido, web..." value={leadForm.source} onChange={(e) => setLeadForm({ ...leadForm, source: e.target.value })} /></Field>
                <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy || (!leadForm.phone && !leadForm.email)}>Cargar prospecto</SmallButton></div>
              </form>
            </Panel>
            <Panel title="Nueva oportunidad">
              <form onSubmit={createOpp} className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <Field label="Titulo"><Input required minLength={3} value={oppForm.title} onChange={(e) => setOppForm({ ...oppForm, title: e.target.value })} /></Field>
                <Field label="Cliente o prospecto">
                  <Select required value={oppForm.account} onChange={(e) => setOppForm({ ...oppForm, account: e.target.value })}>
                    <option value="">Elegir...</option>
                    <optgroup label="Clientes">{(customers.data ?? []).map((c) => <option key={c.id} value={`party:${c.id}`}>{c.legal_name}</option>)}</optgroup>
                    <optgroup label="Prospectos">{(leads.data?.items ?? []).map((l) => <option key={l.id} value={`lead:${l.id}`}>{l.company_name ?? l.contact_name}</option>)}</optgroup>
                  </Select>
                </Field>
                <Field label="Monto estimado (Gs.)"><Input type="number" min="0" step="1" value={oppForm.amount} onChange={(e) => setOppForm({ ...oppForm, amount: e.target.value })} /></Field>
                <Field label="Cierre estimado"><Input type="date" value={oppForm.expected_close_date} onChange={(e) => setOppForm({ ...oppForm, expected_close_date: e.target.value })} /></Field>
                <div><SmallButton type="submit" tone="primary" disabled={action.busy}>Crear oportunidad</SmallButton></div>
              </form>
            </Panel>
          </div>
        )}
      </div>
    </>
  );
}
