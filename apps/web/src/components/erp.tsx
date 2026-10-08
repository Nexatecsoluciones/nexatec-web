"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { Button, NexatecMark } from "@/components/ui";
import { api } from "@/lib/api";
import { ApiError, day, erp, type ErpContext } from "@/lib/erp";

type ErpValue = { ctx: ErpContext; client: ReturnType<typeof erp>; base: string };

const ErpCtx = createContext<ErpValue | null>(null);

export function useErp(): ErpValue {
  const value = useContext(ErpCtx);
  if (!value) throw new Error("useErp fuera de ErpShell");
  return value;
}

const NAV = [
  { href: "", label: "Tablero" },
  { href: "/ventas", label: "Ventas" },
  { href: "/cobranzas", label: "Facturas y cobros" },
  { href: "/compras", label: "Compras" },
  { href: "/pagos", label: "Proveedores y pagos" },
  { href: "/inventario", label: "Inventario" },
  { href: "/productos", label: "Productos" },
  { href: "/terceros", label: "Clientes y proveedores" },
  { href: "/contabilidad", label: "Contabilidad" },
];

function daysLeft(iso: string | null): number | null {
  if (!iso) return null;
  return Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);
}

function DemoBanner({ ctx }: { ctx: ErpContext }) {
  const left = daysLeft(ctx.expires_at);
  const text = encodeURIComponent("Hola NEXATEC, estoy probando la demo del ERP y quiero solicitar mi sistema.");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-amber-300/30 bg-amber-400/10 px-5 py-2 text-sm text-amber-100">
      <p>
        <strong>DEMO</strong> · Empresa y documentos ficticios, sin validez tributaria.
        {left !== null && (left > 0 ? ` Vence en ${left} dia${left === 1 ? "" : "s"} (${day(ctx.expires_at)}).` : " Vencida.")}
      </p>
      <a
        href={`https://wa.me/${ctx.whatsapp_number}?text=${text}`}
        target="_blank"
        rel="noopener noreferrer"
        className="rounded-full bg-amber-300 px-4 py-1 text-xs font-bold text-[#3b2a00]"
      >
        Solicitar mi sistema por WhatsApp
      </a>
    </div>
  );
}

export function ErpShell({ accessId, children }: { accessId: string; children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ctx, setCtx] = useState<ErpContext | null>(null);
  const [error, setError] = useState<string | null>(null);
  const client = erp(accessId);
  const base = `/erp/${accessId}`;

  useEffect(() => {
    client
      .get<ErpContext>("/context")
      .then(setCtx)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setError(err instanceof ApiError ? err.message : "No se pudo abrir el sistema.");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessId]);

  async function logout() {
    await api.logout().catch(() => undefined);
    router.replace("/login");
  }

  if (error) {
    return (
      <main className="mx-auto max-w-xl flex-1 px-5 py-20 text-center">
        <h1 className="text-2xl font-bold">No se puede acceder</h1>
        <p className="mt-3 text-nx-muted">{error}</p>
        <Link href="/portal" className="mt-6 inline-block text-nx-accent">Volver a mis sistemas</Link>
      </main>
    );
  }
  if (!ctx) return <main className="flex-1 py-20 text-center text-nx-muted">Cargando...</main>;

  return (
    <ErpCtx.Provider value={{ ctx, client, base }}>
      <div className="flex min-h-screen flex-1 flex-col lg:flex-row">
        <aside className="border-b border-nx-line bg-black/20 p-4 lg:w-60 lg:border-b-0 lg:border-r">
          <Link href={base} className="mb-5 flex items-center gap-3 px-2">
            <span className="grid h-9 w-9 place-items-center rounded-xl bg-gradient-to-b from-[#0f5d51] to-[#0b4b42]">
              <NexatecMark />
            </span>
            <div>
              <p className="text-sm font-extrabold leading-tight">NEXATEC ERP</p>
              <p className="text-[10px] uppercase tracking-widest text-nx-muted">{ctx.environment === "DEMO" ? "Demo" : "Produccion"}</p>
            </div>
          </Link>
          <nav className="flex flex-row flex-wrap gap-1 lg:flex-col">
            {NAV.map((item) => {
              const href = `${base}${item.href}`;
              const active = item.href === "" ? pathname === base : pathname.startsWith(href);
              return (
                <Link key={item.href} href={href}
                  className={`rounded-xl px-3 py-2 text-sm font-semibold transition ${active ? "bg-nx-accent/15 text-nx-accent" : "text-nx-muted hover:bg-white/5 hover:text-nx-text"}`}>
                  {item.label}
                </Link>
              );
            })}
          </nav>
        </aside>
        <div className="flex min-w-0 flex-1 flex-col">
          {ctx.environment === "DEMO" && <DemoBanner ctx={ctx} />}
          <header className="flex min-h-[60px] items-center justify-between gap-4 border-b border-nx-line bg-black/10 px-5">
            <div>
              <p className="font-bold">{ctx.company_name ?? "Empresa sin configurar"}</p>
              <p className="text-xs text-nx-muted">{ctx.can_write ? "Administrador" : "Solo lectura"}</p>
            </div>
            <div className="flex items-center gap-2">
              <Link href="/portal" className="text-sm text-nx-muted hover:text-nx-text">Mis sistemas</Link>
              <Button variant="secondary" onClick={logout} className="min-h-[36px] px-4 text-xs">Salir</Button>
            </div>
          </header>
          <main className="min-w-0 flex-1 p-5 lg:p-8">{children}</main>
        </div>
      </div>
    </ErpCtx.Provider>
  );
}

// --- Piezas de UI -------------------------------------------------------------

export function PageTitle({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-extrabold">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-nx-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Panel({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`rounded-2xl border border-nx-line bg-nx-card p-5 ${className}`}>
      {title && <h2 className="mb-4 text-sm font-bold uppercase tracking-wider text-nx-muted">{title}</h2>}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-2xl border border-nx-line bg-nx-card p-4" title={hint}>
      <p className="text-xs uppercase tracking-wider text-nx-muted">{label}</p>
      <p className="mt-1 text-xl font-extrabold">{value}</p>
    </div>
  );
}

export function Notice({ kind = "error", children }: { kind?: "error" | "ok" | "info"; children: ReactNode }) {
  const styles = {
    error: "border-red-400/40 bg-red-500/10 text-red-100",
    ok: "border-nx-accent/40 bg-nx-accent/10 text-nx-accent",
    info: "border-nx-line bg-white/5 text-nx-muted",
  }[kind];
  return <div role={kind === "error" ? "alert" : "status"} className={`mb-4 rounded-xl border px-4 py-3 text-sm ${styles}`}>{children}</div>;
}

export interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  align?: "right";
}

export function Table<T>({ rows, columns, empty = "No hay registros.", rowKey }: {
  rows: T[]; columns: Column<T>[]; empty?: string; rowKey: (row: T) => string;
}) {
  if (rows.length === 0) return <p className="py-6 text-center text-sm text-nx-muted">{empty}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead>
          <tr className="border-b border-nx-line text-xs uppercase tracking-wider text-nx-muted">
            {columns.map((c) => (
              <th key={c.header} className={`px-3 py-2 font-semibold ${c.align === "right" ? "text-right" : ""}`}>{c.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)} className="border-b border-nx-line/50 hover:bg-white/5">
              {columns.map((c) => (
                <td key={c.header} className={`px-3 py-2 ${c.align === "right" ? "text-right tabular-nums" : ""}`}>{c.cell(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  if (total <= limit) return null;
  return (
    <div className="mt-3 flex items-center justify-end gap-3 text-sm text-nx-muted">
      <span>{offset + 1}-{Math.min(offset + limit, total)} de {total}</span>
      <button className="rounded-lg px-3 py-1 hover:bg-white/5 disabled:opacity-40" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>Anterior</button>
      <button className="rounded-lg px-3 py-1 hover:bg-white/5 disabled:opacity-40" disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Siguiente</button>
    </div>
  );
}

const inputCls = "w-full rounded-xl border border-nx-line bg-black/20 px-3 py-2 text-sm text-nx-text outline-none focus:border-nx-accent";

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-xs font-semibold text-nx-muted">{label}</span>
      {children}
    </label>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`${inputCls} ${props.className ?? ""}`} />;
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={`${inputCls} ${props.className ?? ""}`} />;
}

export function SmallButton({ children, onClick, disabled, tone = "default", type = "button" }: {
  children: ReactNode; onClick?: () => void; disabled?: boolean; tone?: "default" | "danger" | "primary"; type?: "button" | "submit";
}) {
  const styles = {
    default: "border-nx-line text-nx-text hover:bg-white/5",
    danger: "border-red-400/40 text-red-200 hover:bg-red-500/10",
    primary: "border-nx-accent/50 bg-nx-accent/15 text-nx-accent hover:bg-nx-accent/25",
  }[tone];
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-lg border px-3 py-1 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${styles}`}>
      {children}
    </button>
  );
}

/** Ejecuta una accion contra la API mostrando el error del servidor tal cual. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const run = useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await fn();
      if (ok) setMessage(ok);
      return true;
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Error inesperado.");
      return false;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, error, message, run, setError };
}

/** Carga datos y expone reload; el error de carga se muestra en la pagina. */
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    loader()
      .then((d) => alive && setData(d))
      .catch((err) => alive && setError(err instanceof ApiError ? err.message : "No se pudo cargar."));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);
  return { data, error, reload: () => setTick((t) => t + 1) };
}
