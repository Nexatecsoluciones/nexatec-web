"use client";

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { NexatecMark } from "@/components/ui";
import { CONTACT_EMAIL, LEGAL, whatsappUrl } from "@/lib/site";

const NAV = [
  { href: "/#servicios", label: "Soluciones" },
  { href: "/#planes", label: "Planes" },
  { href: "/#preguntas", label: "Preguntas" },
  { href: "/demo", label: "Probar demo" },
];

export function SiteHeader() {
  const [open, setOpen] = useState(false);
  return (
    <header className="sticky top-0 z-50 border-b border-nx-line bg-[#022b25]/90 backdrop-blur-lg">
      <div className="mx-auto flex min-h-[72px] w-full max-w-[1180px] items-center justify-between gap-4 px-5">
        <Link href="/" className="flex items-center gap-3" aria-label="NEXATEC inicio">
          <span className="grid h-10 w-10 place-items-center rounded-2xl bg-gradient-to-b from-[#0f5d51] to-[#0b4b42]"><NexatecMark /></span>
          <span className="leading-tight">
            <span className="block text-lg font-extrabold tracking-tight">NEXATEC</span>
            <span className="block text-[10px] uppercase tracking-widest text-nx-muted">Soluciones Tecnologicas</span>
          </span>
        </Link>
        <nav className="hidden items-center gap-6 text-sm font-semibold text-[#dce8e5] md:flex">
          {NAV.map((l) => <Link key={l.href} href={l.href} className="hover:text-nx-accent">{l.label}</Link>)}
          <Link href="/login" className="rounded-full border border-nx-line px-4 py-2 hover:border-nx-accent">Acceder a mi sistema</Link>
        </nav>
        <button className="rounded-lg border border-nx-line px-3 py-2 text-sm md:hidden" aria-expanded={open} onClick={() => setOpen(!open)}>
          Menu
        </button>
      </div>
      {open && (
        <nav className="flex flex-col gap-1 border-t border-nx-line px-5 py-3 text-sm font-semibold md:hidden">
          {NAV.map((l) => <Link key={l.href} href={l.href} onClick={() => setOpen(false)} className="py-2">{l.label}</Link>)}
          <Link href="/login" className="py-2 text-nx-accent">Acceder a mi sistema</Link>
        </nav>
      )}
    </header>
  );
}

export function WhatsAppButton({ message, children, variant = "primary" }: { message: string; children: ReactNode; variant?: "primary" | "ghost" }) {
  const cls = variant === "primary"
    ? "bg-gradient-to-b from-nx-accent to-nx-accent-2 text-[#06352f] shadow-[0_14px_34px_rgba(81,234,216,0.22)]"
    : "border border-nx-line text-nx-text hover:border-nx-accent";
  return (
    <a href={whatsappUrl(message)} target="_blank" rel="noopener noreferrer"
      className={`inline-flex min-h-[48px] items-center justify-center rounded-full px-6 font-bold transition hover:-translate-y-0.5 ${cls}`}>
      {children}
    </a>
  );
}

export function SiteFooter() {
  return (
    <footer className="border-t border-nx-line bg-black/20">
      <div className="mx-auto grid w-full max-w-[1180px] grid-cols-1 gap-8 px-5 py-10 text-sm text-nx-muted md:grid-cols-3">
        <div>
          <p className="font-extrabold text-nx-text">NEXATEC Soluciones Tecnologicas</p>
          <p className="mt-2">ERP · CRM · Business Intelligence · Automatizacion</p>
          <p className="mt-2">{LEGAL.tradeName} · {LEGAL.legalName} · RUC {LEGAL.ruc}</p>
          {LEGAL.address && <p>{LEGAL.address}</p>}
        </div>
        <div className="flex flex-col gap-2">
          <a href={whatsappUrl("Hola NEXATEC, quiero hacer una consulta.")} target="_blank" rel="noopener noreferrer" className="hover:text-nx-accent">WhatsApp</a>
          <a href={`mailto:${CONTACT_EMAIL}`} className="hover:text-nx-accent">{CONTACT_EMAIL}</a>
          <Link href="/demo" className="hover:text-nx-accent">Probar una demo gratis</Link>
          <Link href="/login" className="hover:text-nx-accent">Acceder a mi sistema</Link>
        </div>
        <div className="flex flex-col gap-2">
          <Link href="/privacidad" className="hover:text-nx-accent">Politica de privacidad</Link>
          <Link href="/terminos" className="hover:text-nx-accent">Terminos y condiciones</Link>
          <Link href="/cookies" className="hover:text-nx-accent">Cookies</Link>
          <Link href="/admin" className="text-xs opacity-60 hover:text-nx-accent">Administracion</Link>
        </div>
      </div>
      <p className="pb-6 text-center text-xs text-nx-muted">© {new Date().getFullYear()} NEXATEC. Todos los derechos reservados.</p>
    </footer>
  );
}

const COOKIE_KEY = "nx-cookie-notice-v1";

/** Hoy el sitio solo usa la cookie de sesion (estrictamente necesaria) y
 *  ningun tracker: se informa, no se pide consentimiento para algo que no
 *  existe. Si se agregan estadisticas/marketing, tienen que esperar a un
 *  consentimiento explicito antes de cargarse. */
export function CookieNotice() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    try {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShow(!window.localStorage.getItem(COOKIE_KEY));
    } catch {
      setShow(true);
    }
  }, []);
  if (!show) return null;
  return (
    <div role="region" aria-label="Aviso de cookies"
      className="fixed inset-x-3 bottom-3 z-50 mx-auto flex max-w-3xl flex-col gap-3 rounded-2xl border border-nx-line bg-[#03231e]/95 p-4 text-sm shadow-2xl md:flex-row md:items-center">
      <p className="flex-1 text-nx-muted">
        Usamos solo cookies <strong className="text-nx-text">estrictamente necesarias</strong> (para mantener tu sesion). No usamos
        cookies de publicidad ni de seguimiento. <Link href="/cookies" className="text-nx-accent underline">Mas informacion</Link>.
      </p>
      <button className="rounded-full bg-nx-accent px-5 py-2 font-bold text-[#06352f]"
        onClick={() => { try { window.localStorage.setItem(COOKIE_KEY, new Date().toISOString()); } catch { /* sin storage */ } setShow(false); }}>
        Entendido
      </button>
    </div>
  );
}

export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col">
      <SiteHeader />
      <main className="flex-1">{children}</main>
      <SiteFooter />
      <CookieNotice />
    </div>
  );
}

export function Check() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" className="mr-2 inline h-4 w-4 flex-none text-nx-accent">
      <path d="M4 10.5l4 4 8-9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
