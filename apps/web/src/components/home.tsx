import Link from "next/link";
import { Check, WhatsAppButton } from "@/components/site";
import type { HomeContent } from "@/lib/site-content";

// Cuerpo de la pagina de inicio. Lo usan la home publica (contenido
// publicado) y la vista previa del editor del Control Center (borrador).
// Todo el texto llega del CMS y se renderiza como texto.

const DEMO_STEPS = [
  ["1", "Pedi tu demo", "Completas un formulario corto. Revisamos el pedido y te habilitamos."],
  ["2", "Proba 14 dias", "Entras con tu usuario a una empresa de ejemplo con todo cargado."],
  ["3", "Contrata cuando quieras", "Por WhatsApp te armamos tu sistema real, aparte de la demo."],
];

export function Announcement({ text }: { text: string }) {
  if (!text) return null;
  return <div className="bg-nx-accent px-5 py-2 text-center text-sm font-bold text-[#06352f]">{text}</div>;
}

export function HomeBody({ c }: { c: HomeContent }) {
  return (
    <>
      <section className="mx-auto grid w-full max-w-[1180px] grid-cols-1 items-center gap-10 px-5 py-16 lg:grid-cols-[1.2fr_1fr] lg:py-24">
        <div>
          {c.hero.eyebrow && <p className="text-sm font-bold uppercase tracking-widest text-nx-accent">{c.hero.eyebrow}</p>}
          <h1 className="mt-4 text-4xl font-extrabold leading-tight tracking-tight md:text-5xl">{c.hero.title}</h1>
          {c.hero.subtitle && <p className="mt-5 max-w-xl text-lg text-nx-muted">{c.hero.subtitle}</p>}
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/demo" className="inline-flex min-h-[48px] items-center rounded-full bg-gradient-to-b from-nx-accent to-nx-accent-2 px-6 font-bold text-[#06352f]">
              Probar demo gratis
            </Link>
            <WhatsAppButton variant="ghost" message={c.hero.whatsapp_message}>Hablar por WhatsApp</WhatsAppButton>
          </div>
          {c.hero.note && <p className="mt-4 text-sm text-nx-muted">{c.hero.note}</p>}
        </div>
        <div className="rounded-[28px] border border-nx-line bg-nx-card p-6 shadow-[0_28px_90px_rgba(0,0,0,0.3)]">
          <p className="text-xs uppercase tracking-widest text-nx-muted">Tablero de gestion</p>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
            {[["Ventas", "₲ 4.816.000"], ["Margen bruto", "34,4 %"], ["Por cobrar", "₲ 2.315.000"], ["Valor de stock", "₲ 11.573.000"]].map(([k, v]) => (
              <div key={k} className="rounded-2xl border border-nx-line bg-black/20 p-3">
                <p className="text-xs text-nx-muted">{k}</p>
                <p className="mt-1 font-extrabold">{v}</p>
              </div>
            ))}
          </div>
          <p className="mt-3 text-xs text-nx-muted">Datos ilustrativos de la empresa demo.</p>
        </div>
      </section>

      <section id="servicios" className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">{c.services_title}</h2>
        {c.services_subtitle && <p className="mt-2 max-w-2xl text-nx-muted">{c.services_subtitle}</p>}
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {c.services.map((s, i) => (
            <div key={i} className="rounded-2xl border border-nx-line bg-nx-card p-5">
              <h3 className="text-lg font-bold text-nx-accent">{s.title}</h3>
              <p className="mt-2 text-sm text-nx-muted">{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      {c.modules.length > 0 && (
        <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
          <h2 className="text-3xl font-extrabold">{c.modules_title}</h2>
          <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            {c.modules.map((m, i) => (
              <div key={i} className="rounded-2xl border border-nx-line bg-black/10 p-5">
                <h3 className="font-bold">{m.title}</h3>
                <p className="mt-2 text-sm text-nx-muted">{m.text}</p>
              </div>
            ))}
          </div>
        </section>
      )}

      <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Como funciona la demo</h2>
        <ol className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          {DEMO_STEPS.map(([n, t, d]) => (
            <li key={n} className="rounded-2xl border border-nx-line bg-nx-card p-5">
              <span className="grid h-9 w-9 place-items-center rounded-full bg-nx-accent font-extrabold text-[#06352f]">{n}</span>
              <h3 className="mt-3 font-bold">{t}</h3>
              <p className="mt-1 text-sm text-nx-muted">{d}</p>
            </li>
          ))}
        </ol>
        <div className="mt-6"><Link href="/demo" className="font-bold text-nx-accent hover:underline">Pedir mi demo →</Link></div>
      </section>

      <section id="planes" className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">{c.plans_title}</h2>
        {c.plans_subtitle && <p className="mt-2 text-nx-muted">{c.plans_subtitle}</p>}
        <div className={`mt-8 grid grid-cols-1 gap-5 ${c.plans.length >= 3 ? "lg:grid-cols-3" : "lg:grid-cols-2"}`}>
          {c.plans.map((p, i) => (
            <div key={i} className={`flex flex-col rounded-[24px] border p-6 ${p.featured ? "border-nx-accent bg-nx-card" : "border-nx-line bg-black/10"}`}>
              {p.tag && <p className="text-xs font-bold uppercase tracking-widest text-nx-accent">{p.tag}</p>}
              <h3 className="mt-2 text-xl font-extrabold">{p.name}</h3>
              <p className="mt-3 text-2xl font-extrabold">{p.price}</p>
              {p.sub && <p className="text-sm text-nx-muted">{p.sub}</p>}
              {p.text && <p className="mt-3 text-sm text-nx-muted">{p.text}</p>}
              <ul className="mt-4 flex-1 space-y-1 text-sm">
                {p.items.map((item, j) => <li key={j} className="flex items-center"><Check />{item}</li>)}
              </ul>
              <div className="mt-5"><WhatsAppButton variant={p.featured ? "primary" : "ghost"} message={p.whatsapp_message}>Consultar por WhatsApp</WhatsAppButton></div>
            </div>
          ))}
        </div>
      </section>

      {c.sectors.length > 0 && (
        <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
          <h2 className="text-3xl font-extrabold">{c.sectors_title}</h2>
          <div className="mt-6 flex flex-wrap gap-2">
            {c.sectors.map((s, i) => <span key={i} className="rounded-full border border-nx-line px-4 py-2 text-sm">{s}</span>)}
          </div>
        </section>
      )}

      {c.about && (
        <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
          <h2 className="text-3xl font-extrabold">{c.about_title}</h2>
          <p className="mt-4 max-w-3xl whitespace-pre-line text-nx-muted">{c.about}</p>
        </section>
      )}

      {c.faqs.length > 0 && (
        <section id="preguntas" className="mx-auto w-full max-w-[1180px] px-5 py-14">
          <h2 className="text-3xl font-extrabold">{c.faq_title}</h2>
          <div className="mt-6 flex flex-col gap-3">
            {c.faqs.map((f, i) => (
              <details key={i} className="rounded-2xl border border-nx-line bg-black/10 p-5">
                <summary className="cursor-pointer font-bold">{f.question}</summary>
                <p className="mt-3 whitespace-pre-line text-sm text-nx-muted">{f.answer}</p>
              </details>
            ))}
          </div>
        </section>
      )}

      <section className="mx-auto w-full max-w-[1180px] px-5 pb-20 pt-6">
        <div className="flex flex-col items-start justify-between gap-5 rounded-[28px] border border-nx-line bg-nx-card p-8 md:flex-row md:items-center">
          <div>
            <h2 className="text-2xl font-extrabold">{c.cta.title}</h2>
            {c.cta.subtitle && <p className="mt-1 text-nx-muted">{c.cta.subtitle}</p>}
          </div>
          <div className="flex flex-wrap gap-3">
            <WhatsAppButton message={c.cta.whatsapp_message}>Escribir por WhatsApp</WhatsAppButton>
            <Link href="/demo" className="inline-flex min-h-[48px] items-center rounded-full border border-nx-line px-6 font-bold">Probar demo</Link>
          </div>
        </div>
      </section>
    </>
  );
}

/** Si la API no responde: portada minima con los dos caminos de contacto. */
export function HomeFallback() {
  return (
    <section className="mx-auto w-full max-w-[1180px] px-5 py-24">
      <h1 className="text-4xl font-extrabold">NEXATEC Soluciones Tecnologicas</h1>
      <p className="mt-4 text-lg text-nx-muted">ERP, CRM, Business Intelligence y automatizacion para PyMEs.</p>
      <div className="mt-8 flex flex-wrap gap-3">
        <Link href="/demo" className="inline-flex min-h-[48px] items-center rounded-full bg-nx-accent px-6 font-bold text-[#06352f]">Probar demo gratis</Link>
        <WhatsAppButton variant="ghost" message="Hola NEXATEC, quiero hacer una consulta.">Hablar por WhatsApp</WhatsAppButton>
      </div>
    </section>
  );
}
