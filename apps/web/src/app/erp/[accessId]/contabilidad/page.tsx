"use client";

import { useState } from "react";
import { Field, Input, Notice, PageTitle, Pager, Panel, Select, SmallButton, Table, useAction, useErp, useLoad } from "@/components/erp";
import { day, money, type Account, type JournalEntry, type Page } from "@/lib/erp";

type Tab = "comprobacion" | "resultados" | "balance" | "diario" | "periodos";
interface Amount { code: string; name: string; amount: string }
interface TB { notice: string; rows: { code: string; name: string; opening_balance: string; debit: string; credit: string; closing_balance: string }[]; total_debit: string; total_credit: string }
interface PL { notice: string; income: Amount[]; expense: Amount[]; total_income: string; total_expense: string; net_result: string }
interface BS { notice: string; assets: Amount[]; liabilities: Amount[]; equity: Amount[]; current_result: string; total_assets: string; total_liabilities: string; total_equity: string; balanced: boolean }
interface Period { year: number; month: number; status: "OPEN" | "CLOSED"; note: string | null }

const firstOfYear = () => `${new Date().getFullYear()}-01-01`;
const todayIso = () => new Date().toLocaleDateString("en-CA", { timeZone: "America/Asuncion" });
const SOURCE: Record<string, string> = {
  MANUAL: "Manual", STOCK_MOVEMENT: "Stock", SALES_INVOICE: "Factura venta", CUSTOMER_RECEIPT: "Cobro",
  CUSTOMER_RECEIPT_APPLICATION: "Aplic. anticipo", SUPPLIER_INVOICE: "Factura proveedor", SUPPLIER_PAYMENT: "Pago",
  SUPPLIER_PAYMENT_APPLICATION: "Aplic. anticipo prov.",
};

function AmountList({ title, rows, total }: { title: string; rows: Amount[]; total: string }) {
  return (
    <div>
      <h3 className="mb-2 font-bold">{title}</h3>
      {rows.map((r) => (
        <div key={r.code} className="flex justify-between border-b border-nx-line/40 py-1 text-sm">
          <span>{r.code} {r.name}</span><span className="tabular-nums">{money(r.amount)}</span>
        </div>
      ))}
      <div className="flex justify-between py-1 font-bold"><span>Total {title.toLowerCase()}</span><span className="tabular-nums">{money(total)}</span></div>
    </div>
  );
}

export default function ContabilidadPage() {
  const { client, ctx } = useErp();
  const [tab, setTab] = useState<Tab>("comprobacion");
  const [range, setRange] = useState({ from: firstOfYear(), to: todayIso() });
  const [jOffset, setJOffset] = useState(0);
  const [manual, setManual] = useState({ entry_date: todayIso(), description: "", debit_account: "", credit_account: "", amount: "" });
  const action = useAction();

  const accounts = useLoad(() => client.get<Account[]>("/accounting/accounts"), []);
  const tb = useLoad(() => (tab === "comprobacion" ? client.get<TB>("/accounting/reports/trial-balance", { date_from: range.from, date_to: range.to }) : Promise.resolve(null)), [tab, range.from, range.to]);
  const pl = useLoad(() => (tab === "resultados" ? client.get<PL>("/accounting/reports/income-statement", { date_from: range.from, date_to: range.to }) : Promise.resolve(null)), [tab, range.from, range.to]);
  const bs = useLoad(() => (tab === "balance" ? client.get<BS>("/accounting/reports/balance-sheet", { as_of: range.to }) : Promise.resolve(null)), [tab, range.to]);
  const journal = useLoad(() => (tab === "diario" ? client.get<Page<JournalEntry>>("/accounting/entries", { date_from: range.from, date_to: range.to, limit: 20, offset: jOffset }) : Promise.resolve(null)), [tab, range.from, range.to, jOffset]);
  const periods = useLoad(() => (tab === "periodos" ? client.get<Period[]>("/accounting/periods") : Promise.resolve(null)), [tab]);

  const acc = (id: string) => accounts.data?.find((a) => a.id === id);
  const postable = (accounts.data ?? []).filter((a) => a.is_postable && a.is_active);
  const err = accounts.error ?? tb.error ?? pl.error ?? bs.error ?? journal.error ?? periods.error;

  async function postManual(e: React.FormEvent) {
    e.preventDefault();
    const ok = await action.run(() => client.post("/accounting/entries", {
      entry_date: manual.entry_date, description: manual.description,
      lines: [{ account_id: manual.debit_account, debit: manual.amount }, { account_id: manual.credit_account, credit: manual.amount }],
    }), "Asiento registrado.");
    if (ok) {
      setManual({ ...manual, description: "", amount: "" });
      journal.reload();
    }
  }

  async function togglePeriod(p: Period) {
    const note = window.prompt(p.status === "OPEN" ? "Nota del cierre:" : "Motivo de la reapertura:");
    if (note && (await action.run(() => client.post(`/accounting/periods/${p.year}/${p.month}/${p.status === "OPEN" ? "close" : "reopen"}`, { note }),
      p.status === "OPEN" ? "Periodo cerrado." : "Periodo reabierto."))) periods.reload();
  }

  async function reverse(entry: JournalEntry) {
    const reason = window.prompt(`Motivo para revertir ${entry.number}:`);
    if (reason && (await action.run(() => client.post(`/accounting/entries/${entry.id}/reverse`, { reason }), "Asiento revertido."))) journal.reload();
  }

  return (
    <>
      <PageTitle title="Contabilidad" subtitle="Los asientos se generan solos desde cada operacion. Informes de gestion: no reemplazan libros rubricados."
        actions={
          <div className="flex gap-2">
            <Field label="Desde"><Input type="date" value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} /></Field>
            <Field label="Hasta"><Input type="date" value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} /></Field>
          </div>
        } />
      <div className="mb-4 flex flex-wrap gap-2">
        {([["comprobacion", "Balance de comprobacion"], ["resultados", "Estado de resultados"], ["balance", "Balance general"], ["diario", "Libro diario"], ["periodos", "Periodos"]] as [Tab, string][]).map(([k, l]) => (
          <SmallButton key={k} tone={tab === k ? "primary" : "default"} onClick={() => setTab(k)}>{l}</SmallButton>
        ))}
      </div>
      {err && <Notice>{err}</Notice>}
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}

      {tab === "comprobacion" && tb.data && (
        <Panel>
          <Table rows={tb.data.rows} rowKey={(r) => r.code} empty="Sin movimientos en el rango." columns={[
            { header: "Cuenta", cell: (r) => `${r.code} ${r.name}` },
            { header: "Saldo inicial", cell: (r) => money(r.opening_balance), align: "right" },
            { header: "Debe", cell: (r) => money(r.debit), align: "right" },
            { header: "Haber", cell: (r) => money(r.credit), align: "right" },
            { header: "Saldo final", cell: (r) => money(r.closing_balance), align: "right" },
          ]} />
          <p className="mt-3 text-right text-sm font-bold">Debe {money(tb.data.total_debit)} · Haber {money(tb.data.total_credit)}</p>
        </Panel>
      )}

      {tab === "resultados" && pl.data && (
        <Panel>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <AmountList title="Ingresos" rows={pl.data.income} total={pl.data.total_income} />
            <AmountList title="Costos y gastos" rows={pl.data.expense} total={pl.data.total_expense} />
          </div>
          <p className="mt-4 text-right text-lg font-extrabold">Resultado: {money(pl.data.net_result)}</p>
        </Panel>
      )}

      {tab === "balance" && bs.data && (
        <Panel>
          <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
            <AmountList title="Activo" rows={bs.data.assets} total={bs.data.total_assets} />
            <div className="flex flex-col gap-4">
              <AmountList title="Pasivo" rows={bs.data.liabilities} total={bs.data.total_liabilities} />
              <AmountList title="Patrimonio" rows={[...bs.data.equity, { code: "", name: "Resultado del ejercicio", amount: bs.data.current_result }]} total={bs.data.total_equity} />
            </div>
          </div>
          <p className={`mt-4 text-right text-sm font-bold ${bs.data.balanced ? "text-nx-accent" : "text-red-300"}`}>
            {bs.data.balanced ? "Activo = Pasivo + Patrimonio" : "El balance no cuadra: revisar asientos"}
          </p>
        </Panel>
      )}

      {tab === "diario" && (
        <>
          {ctx.can_write && (
            <Panel title="Asiento manual (simple)" className="mb-5">
              <form onSubmit={postManual} className="grid grid-cols-1 gap-3 md:grid-cols-6">
                <Field label="Fecha"><Input type="date" required value={manual.entry_date} onChange={(e) => setManual({ ...manual, entry_date: e.target.value })} /></Field>
                <Field label="Concepto"><Input required minLength={3} value={manual.description} onChange={(e) => setManual({ ...manual, description: e.target.value })} /></Field>
                <Field label="Debe"><Select required value={manual.debit_account} onChange={(e) => setManual({ ...manual, debit_account: e.target.value })}>
                  <option value="">Cuenta...</option>{postable.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</Select></Field>
                <Field label="Haber"><Select required value={manual.credit_account} onChange={(e) => setManual({ ...manual, credit_account: e.target.value })}>
                  <option value="">Cuenta...</option>{postable.map((a) => <option key={a.id} value={a.id}>{a.code} {a.name}</option>)}</Select></Field>
                <Field label="Importe (Gs.)"><Input type="number" min="1" required value={manual.amount} onChange={(e) => setManual({ ...manual, amount: e.target.value })} /></Field>
                <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar</SmallButton></div>
              </form>
            </Panel>
          )}
          <Panel>
            {(journal.data?.items ?? []).length === 0 && <p className="py-6 text-center text-sm text-nx-muted">Sin asientos en el rango.</p>}
            {(journal.data?.items ?? []).map((e) => (
              <div key={e.id} className="mb-4 border-b border-nx-line pb-3">
                <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-sm">
                  <span><strong>{e.number}</strong> · {day(e.entry_date)} · {e.description} <span className="text-nx-muted">({SOURCE[e.source_type] ?? e.source_type})</span></span>
                  {ctx.can_write && e.source_type === "MANUAL" && !e.reverses_entry_id && (
                    <SmallButton tone="danger" disabled={action.busy} onClick={() => reverse(e)}>Revertir</SmallButton>
                  )}
                </div>
                {e.lines.map((l) => (
                  <div key={l.line_no} className={`grid grid-cols-[1fr_120px_120px] text-sm tabular-nums ${Number(l.credit) > 0 ? "pl-6" : ""}`}>
                    <span>{acc(l.account_id)?.code} {acc(l.account_id)?.name}</span>
                    <span className="text-right">{Number(l.debit) ? money(l.debit) : ""}</span>
                    <span className="text-right">{Number(l.credit) ? money(l.credit) : ""}</span>
                  </div>
                ))}
              </div>
            ))}
            <Pager total={journal.data?.total ?? 0} limit={20} offset={jOffset} onChange={setJOffset} />
          </Panel>
        </>
      )}

      {tab === "periodos" && (
        <Panel>
          <Table rows={periods.data ?? []} rowKey={(p) => `${p.year}-${p.month}`} empty="Todavia no hay periodos con movimientos." columns={[
            { header: "Periodo", cell: (p) => `${String(p.month).padStart(2, "0")}/${p.year}` },
            { header: "Estado", cell: (p) => (p.status === "OPEN" ? "Abierto" : "Cerrado") },
            { header: "Nota", cell: (p) => p.note ?? "-" },
            ...(ctx.can_write ? [{ header: "", cell: (p: Period) => (
              <SmallButton tone={p.status === "OPEN" ? "danger" : "default"} disabled={action.busy} onClick={() => togglePeriod(p)}>
                {p.status === "OPEN" ? "Cerrar" : "Reabrir"}
              </SmallButton>
            ) }] : []),
          ]} />
          <p className="mt-3 text-xs text-nx-muted">Con el periodo cerrado no se puede registrar ninguna operacion con fecha en ese mes.</p>
        </Panel>
      )}
    </>
  );
}
