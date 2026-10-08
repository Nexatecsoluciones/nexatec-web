"use client";

import { useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Check, PublicShell, WhatsAppButton } from "@/components/site";
import { useTurnstileToken } from "@/components/turnstile";
import { ApiError, type SystemOut } from "@/lib/api";
import { LEGAL } from "@/lib/site";

const TURNSTILE_SITE_KEY = process.env.NEXT_PUBLIC_TURNSTILE_SITE_KEY ?? "";
const inputCls = "w-full rounded-xl border border-nx-line bg-white/5 px-4 py-3 text-nx-text outline-none focus:border-nx-accent";

export default function DemoPage() {
  const [systems, setSystems] = useState<SystemOut[]>([]);
  const [form, setForm] = useState({ contact_name: "", contact_email: "", contact_phone: "", company_name: "", system_id: "", message: "" });
  const [accept, setAccept] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { token, widget, reset } = useTurnstileToken(TURNSTILE_SITE_KEY);

  useEffect(() => {
    fetch("/api/systems").then((r) => (r.ok ? r.json() : [])).then((all: SystemOut[]) => {
      const demos = all.filter((s) => s.demo_available);
      setSystems(demos);
      if (demos.length === 1) setForm((f) => ({ ...f, system_id: demos[0].id }));
    }).catch(() => setSystems([]));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!accept) {
      setError("Para continuar tenes que aceptar la politica de privacidad.");
      return;
    }
    if (TURNSTILE_SITE_KEY && !token) {
      setError("Completa la verificacion anti-bot.");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/demo-requests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...form, contact_phone: form.contact_phone || null, company_name: form.company_name || null,
          system_id: form.system_id || null, message: form.message || null,
          accept_privacy: true, privacy_version: LEGAL.version, turnstile_token: token,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new ApiError(res.status, typeof body.detail === "string" ? body.detail : "Revisa los datos ingresados.");
      }
      setSent(true);
    } catch (err) {
      reset();
      setError(err instanceof ApiError ? err.message : "No se pudo enviar. Proba de nuevo o escribinos por WhatsApp.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <PublicShell>
      <section className="mx-auto grid w-full max-w-[1180px] grid-cols-1 gap-10 px-5 py-14 lg:grid-cols-[1fr_1.1fr]">
        <div>
          <h1 className="text-4xl font-extrabold tracking-tight">Proba NEXATEC ERP gratis</h1>
          <p className="mt-4 text-nx-muted">14 dias con una empresa de ejemplo ya cargada: productos, clientes, stock, ventas, compras, cobranzas y contabilidad.</p>
          <ul className="mt-6 space-y-2 text-sm">
            <li className="flex items-center"><Check />Sin tarjeta ni pago</li>
            <li className="flex items-center"><Check />Base de datos propia para tu demo</li>
            <li className="flex items-center"><Check />Los documentos de la demo son ficticios y no tienen validez tributaria</li>
            <li className="flex items-center"><Check />Al terminar, si queres, armamos tu sistema real aparte</li>
          </ul>
          <p className="mt-6 text-sm text-nx-muted">Revisamos cada pedido y te avisamos cuando tu demo este lista.</p>
        </div>
        <div className="rounded-[28px] border border-nx-line bg-nx-card p-6">
          {sent ? (
            <div className="flex flex-col gap-4">
              <h2 className="text-2xl font-extrabold">¡Recibimos tu pedido!</h2>
              <p className="text-nx-muted">Te vamos a contactar para habilitar tu demo. Si queres acelerar, escribinos.</p>
              <WhatsAppButton message={`Hola NEXATEC, acabo de pedir una demo para ${form.company_name || "mi empresa"}.`}>Escribir por WhatsApp</WhatsAppButton>
              <Link href="/" className="text-sm text-nx-accent">Volver al inicio</Link>
            </div>
          ) : (
            <form onSubmit={submit} className="flex flex-col gap-3">
              <h2 className="text-xl font-extrabold">Pedir demo</h2>
              <input className={inputCls} required minLength={2} maxLength={200} placeholder="Nombre y apellido" autoComplete="name"
                value={form.contact_name} onChange={(e) => setForm({ ...form, contact_name: e.target.value })} />
              <input className={inputCls} type="email" required placeholder="Email" autoComplete="email"
                value={form.contact_email} onChange={(e) => setForm({ ...form, contact_email: e.target.value })} />
              <input className={inputCls} maxLength={40} placeholder="Telefono / WhatsApp (opcional)" autoComplete="tel"
                value={form.contact_phone} onChange={(e) => setForm({ ...form, contact_phone: e.target.value })} />
              <input className={inputCls} maxLength={200} placeholder="Empresa (opcional)" autoComplete="organization"
                value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} />
              {systems.length > 1 && (
                <select className={inputCls} value={form.system_id} onChange={(e) => setForm({ ...form, system_id: e.target.value })}>
                  <option value="">¿Que sistema queres probar?</option>
                  {systems.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              )}
              <textarea className={inputCls} maxLength={2000} rows={3} placeholder="Contanos de tu negocio (opcional)"
                value={form.message} onChange={(e) => setForm({ ...form, message: e.target.value })} />
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-1" checked={accept} onChange={(e) => setAccept(e.target.checked)} />
                <span>Acepto la <Link href="/privacidad" target="_blank" className="text-nx-accent underline">politica de privacidad</Link> y que NEXATEC me contacte por este pedido.</span>
              </label>
              {widget}
              {error && <p role="alert" className="text-sm font-semibold text-red-300">{error}</p>}
              <button type="submit" disabled={busy}
                className="min-h-[48px] rounded-full bg-gradient-to-b from-nx-accent to-nx-accent-2 font-bold text-[#06352f] disabled:opacity-60">
                {busy ? "Enviando..." : "Pedir mi demo"}
              </button>
            </form>
          )}
        </div>
      </section>
    </PublicShell>
  );
}
