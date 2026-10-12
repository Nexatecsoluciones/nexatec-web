"use client";

import Link from "next/link";
import { useState } from "react";
import { Field, Input, Notice, PageTitle, Panel, Select, SmallButton, Stat, Table, useAction, useErp, useLoad } from "@/components/erp";
import {
  day, label, methodLabel, money, quantity, type CashRegister, type CashSession, type CashSessionDetail, type PosDashboard, type PosSale,
  type Warehouse,
} from "@/lib/erp";

// Billetes y monedas en circulacion en Paraguay (guaranies).
const DENOMS = [100000, 50000, 20000, 10000, 5000, 2000, 1000, 500, 100, 50];
const n = (v: string | number | null | undefined) => Number(v ?? 0) || 0;
const hora = (iso: string) => new Date(iso).toLocaleTimeString("es-PY", { timeZone: "America/Asuncion", hour: "2-digit", minute: "2-digit" });

function SessionView({ d, reload, own, onClosed }: { d: CashSessionDetail; reload: () => void; own: boolean; onClosed?: (id: string) => void }) {
  const { client, can, base } = useErp();
  const action = useAction();
  const [mv, setMv] = useState({ kind: "EXPENSE", amount: "", description: "" });
  const [count, setCount] = useState<Record<number, string>>({});
  const [notes, setNotes] = useState("");
  const sales = useLoad(() => client.get<PosSale[]>("/pos/sales", { session_id: d.session.id }), [d.session.id, d.summary.sales_count]);
  const s = d.session;
  const open = s.status === "OPEN";
  const counted = DENOMS.reduce((acc, v) => acc + v * n(count[v]), 0);
  const expected = n(d.summary.expected_cash);
  const diff = counted - expected;
  const bm = d.summary.by_method;

  return (
    <div className="flex flex-col gap-5">
      {action.error && <Notice>{action.error}</Notice>}
      {action.message && <Notice kind="ok">{action.message}</Notice>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
        <Stat label="Fondo inicial" value={money(s.opening_float)} />
        <Stat label="Efectivo" value={money(bm.CASH ?? 0)} hint="Ventas y cobros en efectivo, menos anulaciones, gastos y retiros" />
        <Stat label="Tarjeta" value={money(bm.CARD ?? 0)} />
        <Stat label="Transferencia" value={money(bm.TRANSFER ?? 0)} />
        <Stat label="Ventas" value={`${d.summary.sales_count} · ${money(d.summary.sales_total)}`} />
        <Stat label="Anuladas" value={String(d.summary.voided_count)} />
        <Stat label={open ? "Debe haber en caja" : "Esperado"} value={money(open ? expected : s.expected_cash)} />
      </div>

      {open && own && (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <Panel title="Ingreso o egreso de efectivo">
            <form className="grid grid-cols-1 gap-3 md:grid-cols-2" onSubmit={async (e) => {
              e.preventDefault();
              if (await action.run(() => client.post(`/pos/sessions/${s.id}/movements`, mv), "Movimiento registrado (con su asiento contable).")) {
                setMv({ ...mv, amount: "", description: "" });
                reload();
              }
            }}>
              <Field label="Tipo">
                <Select value={mv.kind} onChange={(e) => setMv({ ...mv, kind: e.target.value })}>
                  <option value="EXPENSE">Gasto pagado con la caja</option>
                  <option value="TO_BANK">Retiro / deposito al banco</option>
                  <option value="CASH_IN">Otro ingreso</option>
                  <option value="FROM_BANK">Cambio traido del banco</option>
                </Select>
              </Field>
              <Field label="Monto (Gs.)"><Input type="number" min="1" step="1" required value={mv.amount} onChange={(e) => setMv({ ...mv, amount: e.target.value })} /></Field>
              <div className="md:col-span-2"><Field label="Detalle"><Input required minLength={3} placeholder="Ej.: compra de bolsas, deposito Banco X" value={mv.description} onChange={(e) => setMv({ ...mv, description: e.target.value })} /></Field></div>
              <div><SmallButton type="submit" tone="primary" disabled={action.busy}>Registrar</SmallButton></div>
            </form>
          </Panel>

          <Panel title="Cerrar caja (arqueo)">
            <p className="mb-3 text-xs text-nx-muted">Conta el efectivo por billete; el total y la diferencia se calculan solos.</p>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
              {DENOMS.map((v) => (
                <label key={v} className="flex flex-col text-xs">
                  <span className="text-nx-muted">{money(v)}</span>
                  <input type="number" min="0" step="1" inputMode="numeric" value={count[v] ?? ""} aria-label={`Cantidad de ${v}`}
                    onChange={(e) => setCount({ ...count, [v]: e.target.value })}
                    className="rounded-lg border border-nx-line bg-white/5 px-2 py-1.5 text-sm" />
                </label>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-center text-sm">
              <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">Contado</p><p className="font-bold">{money(counted)}</p></div>
              <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">Debe haber</p><p className="font-bold">{money(expected)}</p></div>
              <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">Diferencia</p>
                <p className={`font-extrabold ${diff === 0 ? "text-nx-accent" : "text-red-300"}`}>{diff > 0 ? "+" : ""}{money(diff)}</p></div>
            </div>
            <Input className="mt-3" placeholder="Observaciones del cierre (opcional)" value={notes} onChange={(e) => setNotes(e.target.value)} />
            <div className="mt-3"><SmallButton tone="primary" disabled={action.busy} onClick={async () => {
              const detail = Object.fromEntries(DENOMS.filter((v) => n(count[v]) > 0).map((v) => [String(v), n(count[v])]));
              const txt = diff === 0 ? "La caja cuadra." : `Hay una diferencia de ${money(diff)} (${diff > 0 ? "sobrante" : "faltante"}); se registra en contabilidad.`;
              if (window.confirm(`${txt}\n\n¿Cerrar el turno ${s.number}? Despues no se puede modificar.`) &&
                  await action.run(() => client.post(`/pos/sessions/${s.id}/close`, { counted_cash: String(counted), count_detail: detail, notes: notes || null }), "Turno cerrado.")) {
                if (onClosed) onClosed(s.id);
                else reload();
              }
            }}>Cerrar turno</SmallButton></div>
          </Panel>
        </div>
      )}

      {!open && (
        <Panel title={`Arqueo del turno ${s.number}`}>
          <p className="text-sm">Contado <strong>{money(s.counted_cash)}</strong> · Esperado <strong>{money(s.expected_cash)}</strong> · Diferencia{" "}
            <strong className={n(s.difference) === 0 ? "text-nx-accent" : "text-red-300"}>{money(s.difference)}</strong>{s.closing_notes && ` · ${s.closing_notes}`}</p>
          {s.count_detail && <p className="mt-1 text-xs text-nx-muted">{Object.entries(s.count_detail).map(([k, v]) => `${v} x ${money(k)}`).join(" · ")}</p>}
        </Panel>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
        <Panel title="Ventas del turno">
          <Table compact rows={sales.data ?? []} rowKey={(x) => x.id} empty="Sin ventas." columns={[
            { header: "Hora", cell: (x) => hora(x.created_at) },
            { header: "Comprobante", cell: (x) => <span className={x.status === "VOIDED" ? "text-nx-muted line-through" : ""}>{x.invoice_number}</span> },
            { header: "Total", cell: (x) => money(x.total), align: "right" },
            { header: "", cell: (x) => (
              <div className="flex justify-end gap-1">
                <a className="rounded-full border border-nx-line px-3 py-1 text-xs" target="_blank" rel="noopener noreferrer"
                  href={`${base.replace("/erp/", "/imprimir/")}/ticket/${x.id}`}>Ticket</a>
                {x.status === "COMPLETED" && open && can("cash:manage") && (
                  <SmallButton tone="danger" disabled={action.busy} onClick={async () => {
                    const reason = window.prompt(`Motivo de la anulacion de ${x.invoice_number} (devuelve stock, anula cobros y emite nota de credito):`);
                    if (reason && await action.run(() => client.post(`/pos/sales/${x.id}/void`, { reason }), "Venta anulada.")) { reload(); sales.reload(); }
                  }}>Anular</SmallButton>
                )}
                {x.status === "VOIDED" && <span className="text-xs text-red-300">Anulada</span>}
              </div>
            ) },
          ]} />
        </Panel>
        <Panel title="Movimientos">
          <Table compact rows={d.movements} rowKey={(m) => m.id} empty="Sin movimientos." columns={[
            { header: "Hora", cell: (m) => hora(m.created_at) },
            { header: "Movimiento", cell: (m) => <><p>{label(m.kind)} · {methodLabel(m.method)}</p>{m.description && <p className="text-xs text-nx-muted">{m.description}</p>}</> },
            { header: "Monto", cell: (m) => <span className={m.direction < 0 ? "text-red-300" : ""}>{m.direction < 0 ? "-" : "+"}{money(m.amount)}</span>, align: "right" },
          ]} />
        </Panel>
      </div>
    </div>
  );
}

function Dashboard() {
  const { client } = useErp();
  const [date, setDate] = useState("");
  const d = useLoad(() => client.get<PosDashboard>("/pos/dashboard", { day: date || undefined }), [date]);
  const x = d.data;
  return (
    <div className="flex flex-col gap-5">
      <div className="w-48"><Field label="Dia"><Input type="date" value={date} onChange={(e) => setDate(e.target.value)} /></Field></div>
      {d.error && <Notice>{d.error}</Notice>}
      {x && <>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
          <Stat label="Ventas" value={`${x.sales_count} · ${money(x.sales_total)}`} />
          <Stat label="Ticket promedio" value={money(x.average_ticket)} />
          <Stat label="Efectivo (neto de vuelto)" value={money(x.by_method.CASH ?? 0)} />
          <Stat label="Tarjeta / transferencia" value={`${money(x.by_method.CARD ?? 0)} / ${money(x.by_method.TRANSFER ?? 0)}`} />
          <Stat label="A credito / anuladas" value={`${money(x.credit_total)} / ${x.voided_count}`} />
        </div>
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Panel title="Por cajero">
            <Table compact rows={x.by_user} rowKey={(u) => u.user ?? "-"} empty="Sin ventas." columns={[
              { header: "Cajero", cell: (u) => u.user ?? "-" }, { header: "Ventas", cell: (u) => u.count, align: "right" },
              { header: "Total", cell: (u) => money(u.total), align: "right" }]} />
          </Panel>
          <Panel title="Productos mas vendidos">
            <Table compact rows={x.top_products} rowKey={(p) => p.product_id} empty="Sin ventas." columns={[
              { header: "Producto", cell: (p) => p.name }, { header: "Cant.", cell: (p) => quantity(p.quantity), align: "right" },
              { header: "Total", cell: (p) => money(p.total), align: "right" }]} />
          </Panel>
          <Panel title="Turnos">
            <Table compact rows={x.sessions} rowKey={(s) => s.id} empty="Sin turnos." columns={[
              { header: "Turno", cell: (s) => <><p className="font-semibold">{s.number} · {s.register}</p><p className="text-xs text-nx-muted">{s.opened_by} · {hora(s.opened_at)}</p></> },
              { header: "Estado", cell: (s) => (s.status === "OPEN" ? "Abierto" : "Cerrado") },
              { header: "Diferencia", cell: (s) => (s.difference === null ? "-" : <span className={n(s.difference) === 0 ? "text-nx-accent" : "text-red-300"}>{money(s.difference)}</span>), align: "right" }]} />
          </Panel>
        </div>
      </>}
    </div>
  );
}

function Registers() {
  const { client } = useErp();
  const action = useAction();
  const regs = useLoad(() => client.get<CashRegister[]>("/pos/registers"), []);
  const whs = useLoad(() => client.get<Warehouse[]>("/warehouses"), []);
  const [f, setF] = useState({ code: "", name: "", warehouse_id: "" });
  const wh = (id: string) => whs.data?.find((w) => w.id === id)?.name ?? "-";
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Panel title="Cajas">
        <Table compact rows={regs.data ?? []} rowKey={(r) => r.id} empty="Sin cajas." columns={[
          { header: "Caja", cell: (r) => `${r.code} - ${r.name}` }, { header: "Vende desde", cell: (r) => wh(r.warehouse_id) },
          { header: "Estado", cell: (r) => (r.is_active ? "Activa" : "Inactiva") }]} />
      </Panel>
      <Panel title="Nueva caja">
        {action.error && <Notice>{action.error}</Notice>}
        <form className="grid grid-cols-1 gap-3 md:grid-cols-2" onSubmit={async (e) => {
          e.preventDefault();
          if (await action.run(() => client.post("/pos/registers", f), "Caja creada.")) { setF({ code: "", name: "", warehouse_id: "" }); regs.reload(); }
        }}>
          <Field label="Codigo"><Input required placeholder="CAJA-02" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} /></Field>
          <Field label="Nombre"><Input required minLength={2} placeholder="Caja mostrador 2" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label="Deposito del que vende">
            <Select required value={f.warehouse_id} onChange={(e) => setF({ ...f, warehouse_id: e.target.value })}>
              <option value="">Elegir...</option>
              {(whs.data ?? []).filter((w) => w.is_active).map((w) => <option key={w.id} value={w.id}>{w.code} - {w.name}</option>)}
            </Select>
          </Field>
          <div className="flex items-end"><SmallButton type="submit" tone="primary" disabled={action.busy}>Crear caja</SmallButton></div>
        </form>
      </Panel>
    </div>
  );
}

export default function CajaPage() {
  const { client, can, base } = useErp();
  const manage = can("cash:manage");
  const [tab, setTab] = useState<"mia" | "tablero" | "turnos" | "cajas">("mia");
  const [viewing, setViewing] = useState<string | null>(null);
  const [closedMsg, setClosedMsg] = useState<string | null>(null);
  const mine = useLoad(async () => ({ current: await client.get<CashSessionDetail | null>("/pos/session/current") }), []);
  const list = useLoad(() => client.get<CashSession[]>("/pos/sessions"), [tab]);
  const other = useLoad(() => (viewing ? client.get<CashSessionDetail>(`/pos/sessions/${viewing}`) : Promise.resolve(null)), [viewing]);
  const tabs = [["mia", "Mi caja"], ...(manage ? [["tablero", "Tablero del dia"], ["turnos", "Turnos"], ["cajas", "Cajas"]] : [["turnos", "Mis turnos"]])] as const;

  return (
    <>
      <PageTitle title="Caja" subtitle="Turnos, efectivo, arqueo y ventas del dia" />
      <nav className="mb-5 flex flex-wrap gap-2">
        {tabs.map(([k, l]) => (
          <button key={k} onClick={() => { setTab(k as typeof tab); setViewing(null); setClosedMsg(null); }}
            className={`rounded-full px-4 py-1.5 text-sm font-semibold ${tab === k ? "bg-nx-accent text-[#06352f]" : "border border-nx-line text-nx-muted"}`}>{l}</button>
        ))}
      </nav>
      {tab === "mia" && (mine.error ? <Notice>{mine.error}</Notice> : !mine.data ? <p className="text-nx-muted">Cargando...</p> :
        mine.data.current ? <SessionView d={mine.data.current} own reload={() => { mine.reload(); list.reload(); }}
          onClosed={(id) => {
            // Al cerrar, mostrar el arqueo del turno (no una pantalla vacia).
            setClosedMsg("Turno cerrado. Este es el resumen de tu arqueo.");
            mine.reload();
            setTab("turnos");
            setViewing(id);
          }} /> : (
          <Notice kind="info">No tenes un turno abierto. <Link className="font-bold text-nx-accent underline" href={`${base}/vender`}>Abri la caja desde Vender</Link>.</Notice>
        ))}
      {tab === "tablero" && manage && <Dashboard />}
      {tab === "cajas" && manage && <Registers />}
      {tab === "turnos" && (
        viewing && other.data ? (
          <>
            {closedMsg && <Notice kind="ok">{closedMsg}</Notice>}
            <div className="mb-3"><SmallButton onClick={() => { setViewing(null); setClosedMsg(null); }}>Volver a la lista</SmallButton></div>
            <SessionView d={other.data} own={false} reload={() => other.reload()} />
          </>
        ) : (
          <Panel>
            <Table compact rows={list.data ?? []} rowKey={(s) => s.id} empty="Sin turnos." columns={[
              { header: "Turno", cell: (s) => <button className="text-nx-accent hover:underline" onClick={() => setViewing(s.id)}>{s.number}</button> },
              { header: "Cajero", cell: (s) => s.opened_by ?? "-" },
              { header: "Apertura", cell: (s) => `${day(s.opened_at)} ${hora(s.opened_at)}` },
              { header: "Estado", cell: (s) => (s.status === "OPEN" ? "Abierto" : "Cerrado") },
              { header: "Diferencia", cell: (s) => (s.difference === null ? "-" : <span className={n(s.difference) === 0 ? "text-nx-accent" : "text-red-300"}>{money(s.difference)}</span>), align: "right" },
            ]} />
          </Panel>
        )
      )}
    </>
  );
}
