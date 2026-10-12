"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Field, Input, Notice, Select, SmallButton, useAction, useErp, useLoad } from "@/components/erp";
import {
  ApiError, money, newKey, type CashRegister, type CashSessionDetail, type Page, type Party, type PosProduct, type PosSale,
} from "@/lib/erp";

// Venta rapida. El foco vuelve siempre al buscador: los lectores de codigo
// de barras USB/Bluetooth "tipean" el codigo y Enter. Enter con coincidencia
// exacta (codigo o SKU) agrega al carrito; si no, muestra resultados.
// Una clave de idempotencia por venta (la misma en cada reintento).

type Line = { product: PosProduct; quantity: number; discount: number };
type Pay = { CASH: string; CARD: string; TRANSFER: string; ref: string };
const EMPTY_PAY: Pay = { CASH: "", CARD: "", TRANSFER: "", ref: "" };
const n = (v: string | number | null | undefined) => Number(v ?? 0) || 0;

function OpenRegister({ onOpen }: { onOpen: () => void }) {
  const { client, can } = useErp();
  const action = useAction();
  const regs = useLoad(() => client.get<CashRegister[]>("/pos/registers"), []);
  const [form, setForm] = useState({ register_id: "", opening_float: "" });
  const active = (regs.data ?? []).filter((r) => r.is_active);
  return (
    <div className="mx-auto mt-10 max-w-md rounded-3xl border border-nx-line bg-nx-card p-6">
      <h1 className="text-2xl font-extrabold">Abrir caja</h1>
      <p className="mt-1 text-sm text-nx-muted">Para vender necesitas tu turno de caja abierto. Conta el efectivo inicial (fondo de cambio).</p>
      {action.error && <div className="mt-3"><Notice>{action.error}</Notice></div>}
      {regs.data && active.length === 0 && (
        <div className="mt-4"><Notice kind="info">No hay cajas configuradas.{can("cash:manage") ? " Crealas en Caja > Cajas." : " Pedile a un supervisor que cree una."}</Notice></div>
      )}
      <form className="mt-4 flex flex-col gap-3" onSubmit={async (e) => {
        e.preventDefault();
        if (await action.run(() => client.post("/pos/sessions", { register_id: form.register_id, opening_float: form.opening_float || "0" }))) onOpen();
      }}>
        <Field label="Caja">
          <Select required value={form.register_id} onChange={(e) => setForm({ ...form, register_id: e.target.value })}>
            <option value="">Elegir...</option>
            {active.map((r) => <option key={r.id} value={r.id}>{r.code} - {r.name}</option>)}
          </Select>
        </Field>
        <Field label="Fondo inicial en efectivo (Gs.)">
          <Input type="number" min="0" step="1" inputMode="numeric" value={form.opening_float} onChange={(e) => setForm({ ...form, opening_float: e.target.value })} />
        </Field>
        <SmallButton type="submit" tone="primary" disabled={action.busy}>Abrir caja</SmallButton>
      </form>
    </div>
  );
}

export default function VenderPage() {
  const { client, can, base } = useErp();
  // Envuelto en un objeto: data=null significa "cargando"; current=null, "sin caja abierta".
  const session = useLoad(async () => ({ current: await client.get<CashSessionDetail | null>("/pos/session/current") }), []);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PosProduct[]>([]);
  const [cart, setCart] = useState<Line[]>([]);
  const [customer, setCustomer] = useState<Party | null>(null);
  const [custQ, setCustQ] = useState("");
  const [custResults, setCustResults] = useState<Party[]>([]);
  const [credit, setCredit] = useState(false);
  const [pay, setPay] = useState<Pay>(EMPTY_PAY);
  const [saleKey, setSaleKey] = useState(newKey);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [done, setDone] = useState<PosSale | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const discount = can("pos:discount");

  const focusSearch = useCallback(() => setTimeout(() => searchRef.current?.focus(), 0), []);

  const add = useCallback((p: PosProduct, qty = 1) => {
    setCart((c) => {
      const i = c.findIndex((l) => l.product.product_id === p.product_id);
      if (i >= 0) return c.map((l, j) => (j === i ? { ...l, quantity: l.quantity + qty } : l));
      return [...c, { product: p, quantity: qty, discount: 0 }];
    });
    setDone(null);
    setMsg(null);
    setQ("");
    setResults([]);
    focusSearch();
  }, [focusSearch]);

  // Busqueda mientras se escribe (no para lecturas de escaner, que llegan con Enter).
  useEffect(() => {
    const term = q.trim();
    if (term.length < 2) return;
    const t = setTimeout(() => {
      client.get<PosProduct[]>("/pos/search", { q: term }).then(setResults).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q, client]);

  useEffect(() => {
    const term = custQ.trim();
    if (term.length < 2) return;
    const t = setTimeout(() => {
      client.get<Page<Party>>("/parties", { role: "customer", q: term, limit: 8 }).then((r) => setCustResults(r.items)).catch(() => setCustResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [custQ, client]);

  async function onEnter() {
    const code = q.trim();
    if (!code) return;
    // "3*codigo" = cantidad 3
    const m = code.match(/^(\d+(?:[.,]\d+)?)\*(.+)$/);
    const qty = m ? Number(m[1].replace(",", ".")) : 1;
    const term = m ? m[2] : code;
    try {
      const p = await client.get<PosProduct | null>("/pos/lookup", { code: term });
      if (p) return add(p, qty);
      if (results.length === 1) return add(results[0], qty);
      setMsg({ kind: "error", text: `No hay un producto con el codigo "${term}". Elegilo de la lista o revisa el codigo.` });
    } catch (err) {
      setMsg({ kind: "error", text: err instanceof ApiError ? err.message : "No se pudo buscar." });
    }
  }

  const total = useMemo(() => cart.reduce((s, l) => s + Math.round(n(l.product.sale_price) * l.quantity * (1 - l.discount / 100)), 0), [cart]);
  const nonCash = n(pay.CARD) + n(pay.TRANSFER);
  const cash = n(pay.CASH);
  const paid = Math.min(total, nonCash + cash);
  const change = Math.max(0, cash - Math.max(0, total - nonCash));
  const missing = Math.max(0, total - nonCash - cash);
  const canCharge = cart.length > 0 && !busy && (credit ? !!customer && change === 0 : missing === 0) && nonCash <= total;

  const reset = useCallback(() => {
    setCart([]);
    setPay(EMPTY_PAY);
    setCustomer(null);
    setCredit(false);
    setSaleKey(newKey());
    focusSearch();
  }, [focusSearch]);

  const charge = useCallback(async () => {
    if (!canCharge) return;
    setBusy(true);
    setMsg(null);
    const payments = (["CARD", "TRANSFER", "CASH"] as const).filter((k) => n(pay[k]) > 0)
      .map((k) => ({ method: k, amount: String(n(pay[k])), reference: k !== "CASH" && pay.ref ? pay.ref : null }));
    try {
      const sale = await client.postKeyed<PosSale>("/pos/sales", {
        customer_id: customer?.id ?? null, condition: credit ? "CREDIT" : "CASH",
        lines: cart.map((l) => ({ product_id: l.product.product_id, quantity: String(l.quantity),
          ...(l.discount ? { discount_pct: String(l.discount) } : {}) })),
        payments,
      }, saleKey);
      setDone(sale);
      session.reload();
      reset();
    } catch (err) {
      // Error de red: se puede reintentar con la MISMA clave sin duplicar.
      setMsg({ kind: "error", text: err instanceof ApiError ? err.message : "Sin conexion: toca Cobrar de nuevo (no se duplica)." });
    } finally {
      setBusy(false);
    }
  }, [canCharge, pay, client, customer, credit, cart, saleKey, session, reset]);

  // Atajos: F2 cobrar, Esc limpiar; cualquier tecla fuera de un campo vuelve al buscador.
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "F2") { e.preventDefault(); charge(); return; }
      if (e.key === "Escape") { setQ(""); setResults([]); focusSearch(); return; }
      const tag = (document.activeElement?.tagName ?? "").toLowerCase();
      if (!["input", "select", "textarea", "button"].includes(tag) && e.key.length === 1) searchRef.current?.focus();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [charge, focusSearch]);

  if (session.error) return <Notice>{session.error}</Notice>;
  if (!session.data) return <p className="text-nx-muted">Cargando caja...</p>;
  if (!session.data.current) return <OpenRegister onOpen={() => session.reload()} />;
  const s = session.data.current;
  const setQty = (i: number, qty: number) => setCart((c) => (qty <= 0 ? c.filter((_, j) => j !== i) : c.map((l, j) => (j === i ? { ...l, quantity: qty } : l))));

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <section className="flex min-w-0 flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-nx-muted">
          <span>Turno <strong className="text-nx-text">{s.session.number}</strong> · {s.summary.sales_count} venta(s) · {money(s.summary.sales_total)}</span>
          <span>F2 cobrar · Esc limpiar · &quot;3*codigo&quot; = 3 unidades</span>
        </div>
        <input ref={searchRef} autoFocus value={q} placeholder="Escanear codigo o buscar por nombre / SKU"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onEnter(); } }}
          className="w-full rounded-2xl border-2 border-nx-accent/60 bg-black/30 px-5 py-4 text-xl outline-none focus:border-nx-accent"
          aria-label="Buscar producto o escanear codigo" />
        {msg && <Notice kind={msg.kind === "ok" ? "ok" : "error"}>{msg.text}</Notice>}
        {results.length > 0 && (
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {results.map((p) => (
              <button key={p.product_id} onClick={() => add(p)}
                className="flex items-center justify-between rounded-2xl border border-nx-line bg-nx-card px-4 py-3 text-left hover:border-nx-accent">
                <span><span className="block font-semibold">{p.name}</span><span className="text-xs text-nx-muted">{p.sku}{p.available !== null && ` · stock ${n(p.available)}`}</span></span>
                <span className="font-bold">{money(p.sale_price)}</span>
              </button>
            ))}
          </div>
        )}
        {done && (
          <div className="rounded-3xl border border-nx-accent bg-nx-accent/10 p-5">
            <p className="text-lg font-extrabold text-nx-accent">Venta registrada · {done.invoice_number}</p>
            <p className="mt-1 text-sm">Total {money(done.total)}{n(done.change_given) > 0 && <> · <strong className="text-2xl">Vuelto {money(done.change_given)}</strong></>}</p>
            <div className="mt-3 flex gap-2">
              <a className="rounded-full bg-nx-accent px-4 py-2 text-sm font-bold text-[#06352f]" target="_blank" rel="noopener noreferrer"
                href={`${base.replace("/erp/", "/imprimir/")}/ticket/${done.id}`}>Imprimir ticket</a>
              <SmallButton onClick={() => { setDone(null); focusSearch(); }}>Nueva venta</SmallButton>
            </div>
          </div>
        )}
      </section>

      <aside className="flex flex-col gap-3 rounded-3xl border border-nx-line bg-nx-card p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-extrabold">Carrito</h2>
          {cart.length > 0 && <button className="text-xs text-red-300 hover:underline" onClick={reset}>Vaciar</button>}
        </div>
        {cart.length === 0 ? <p className="py-6 text-center text-sm text-nx-muted">Escanea o busca un producto.</p> : (
          <ul className="flex max-h-[40vh] flex-col gap-2 overflow-auto">
            {cart.map((l, i) => (
              <li key={l.product.product_id} className="rounded-xl border border-nx-line/60 p-2 text-sm">
                <div className="flex justify-between gap-2"><span className="font-semibold">{l.product.name}</span>
                  <span className="font-bold">{money(Math.round(n(l.product.sale_price) * l.quantity * (1 - l.discount / 100)))}</span></div>
                <div className="mt-1 flex items-center gap-2">
                  <button aria-label="Menos" className="h-8 w-8 rounded-lg border border-nx-line" onClick={() => setQty(i, l.quantity - 1)}>-</button>
                  <input aria-label="Cantidad" className="w-16 rounded-lg border border-nx-line bg-transparent px-2 py-1 text-center" type="number" min="0" step="1"
                    value={l.quantity} onChange={(e) => setQty(i, Number(e.target.value))} />
                  <button aria-label="Mas" className="h-8 w-8 rounded-lg border border-nx-line" onClick={() => setQty(i, l.quantity + 1)}>+</button>
                  <span className="text-xs text-nx-muted">x {money(l.product.sale_price)}</span>
                  {discount && (
                    <label className="ml-auto flex items-center gap-1 text-xs">Desc.%
                      <input className="w-14 rounded-lg border border-nx-line bg-transparent px-1 py-1 text-center" type="number" min="0" max="100"
                        value={l.discount} onChange={(e) => setCart((c) => c.map((x, j) => (j === i ? { ...x, discount: Math.min(100, Math.max(0, Number(e.target.value))) } : x)))} />
                    </label>
                  )}
                </div>
                {l.product.available !== null && l.quantity > n(l.product.available) && <p className="mt-1 text-xs text-red-300">Stock disponible: {n(l.product.available)}</p>}
              </li>
            ))}
          </ul>
        )}

        <div className="rounded-2xl bg-black/30 p-3">
          <div className="flex items-baseline justify-between"><span className="text-sm text-nx-muted">TOTAL (IVA incluido)</span>
            <span className="text-3xl font-extrabold tabular-nums">{money(total)}</span></div>
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex gap-2 text-sm">
            <button className={`flex-1 rounded-full px-3 py-2 font-bold ${!credit ? "bg-nx-accent text-[#06352f]" : "border border-nx-line"}`} onClick={() => setCredit(false)}>Contado</button>
            <button className={`flex-1 rounded-full px-3 py-2 font-bold ${credit ? "bg-nx-accent text-[#06352f]" : "border border-nx-line"}`} onClick={() => setCredit(true)}>Credito</button>
          </div>
          <div className="relative">
            <Input placeholder={customer ? customer.legal_name : "Cliente: Consumidor final (buscar por nombre o RUC)"} value={custQ}
              onChange={(e) => { setCustQ(e.target.value); if (e.target.value.trim().length < 2) setCustResults([]); }} />
            {custResults.length > 0 && custQ.trim().length >= 2 && (
              <div className="absolute z-10 mt-1 w-full rounded-xl border border-nx-line bg-[#03231e] p-1 shadow-xl">
                {custResults.map((c) => (
                  <button key={c.id} className="block w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-white/10"
                    onClick={() => { setCustomer(c); setCustQ(""); setCustResults([]); }}>
                    {c.legal_name}{c.ruc && <span className="text-xs text-nx-muted"> · RUC {c.ruc}-{c.ruc_dv}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>
          {customer && <p className="text-xs text-nx-muted">Cliente: <strong className="text-nx-text">{customer.legal_name}</strong> <button className="text-red-300" onClick={() => setCustomer(null)}>quitar</button></p>}
          {credit && !customer && <p className="text-xs text-amber-300">Para vender a credito elegi el cliente.</p>}
        </div>

        <div className="grid grid-cols-3 gap-2">
          {(["CASH", "CARD", "TRANSFER"] as const).map((k) => (
            <Field key={k} label={k === "CASH" ? "Efectivo" : k === "CARD" ? "Tarjeta" : "Transferencia"}>
              <Input type="number" min="0" step="1" inputMode="numeric" value={pay[k]} onChange={(e) => setPay({ ...pay, [k]: e.target.value })} />
            </Field>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          <button className="rounded-full border border-nx-line px-3 py-1 text-xs" disabled={!total} onClick={() => setPay({ ...pay, CASH: String(Math.max(0, total - nonCash)) })}>Justo</button>
          {[20000, 50000, 100000].map((v) => (
            <button key={v} className="rounded-full border border-nx-line px-3 py-1 text-xs" onClick={() => setPay({ ...pay, CASH: String(cash + v) })}>+{money(v)}</button>
          ))}
          {(n(pay.CARD) > 0 || n(pay.TRANSFER) > 0) && (
            <input className="ml-auto w-36 rounded-full border border-nx-line bg-transparent px-3 py-1 text-xs" placeholder="Nro. voucher / ref." value={pay.ref} onChange={(e) => setPay({ ...pay, ref: e.target.value })} />
          )}
        </div>
        <div className="grid grid-cols-3 gap-2 text-center text-sm">
          <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">Cobrado</p><p className="font-bold">{money(paid)}</p></div>
          <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">{credit ? "Queda a credito" : "Falta"}</p><p className={`font-bold ${missing > 0 && !credit ? "text-red-300" : ""}`}>{money(missing)}</p></div>
          <div className="rounded-xl bg-black/20 p-2"><p className="text-xs text-nx-muted">Vuelto</p><p className="text-lg font-extrabold text-nx-accent">{money(change)}</p></div>
        </div>
        <button onClick={charge} disabled={!canCharge}
          className="min-h-[60px] rounded-2xl bg-gradient-to-b from-nx-accent to-nx-accent-2 text-xl font-extrabold text-[#06352f] disabled:opacity-40">
          {busy ? "Registrando..." : credit ? "Registrar venta a credito (F2)" : "Cobrar (F2)"}
        </button>
        <p className="text-center text-[11px] text-nx-muted">Comprobante interno: SIN VALIDEZ TRIBUTARIA (SIFEN no habilitado).</p>
      </aside>
    </div>
  );
}
