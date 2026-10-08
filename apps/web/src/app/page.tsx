import type { Metadata } from "next";
import Link from "next/link";
import { Check, PublicShell, WhatsAppButton } from "@/components/site";
import { isProduction } from "@/lib/site";

// El meta robots depende del entorno del servidor (staging nunca indexable).
export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: { absolute: "NEXATEC | ERP, CRM y Business Intelligence para PyMEs en Paraguay" },
  description:
    "Sistema de gestion para PyMEs paraguayas: ventas, stock, compras, cobranzas, contabilidad y tableros. Proba una demo gratis de 14 dias.",
  robots: { index: isProduction(), follow: isProduction() },
  openGraph: {
    title: "NEXATEC | Tecnologia a medida para tu PyME",
    description: "ERP, CRM, Business Intelligence y automatizacion. Demo gratis de 14 dias.",
    locale: "es_PY",
    type: "website",
  },
};

const SERVICES = [
  { title: "ERP", text: "Ventas, caja, stock, compras, cobranzas, pagos y contabilidad conectados: cada operacion genera su asiento." },
  { title: "CRM", text: "Seguimiento de clientes, oportunidades, agenda y atencion para vender mas y no perder contactos." },
  { title: "Business Intelligence", text: "Tableros con ventas, margen, cartera, stock y caja para decidir con datos reales." },
  { title: "Automatizacion", text: "Alertas, recordatorios, reportes programados e integraciones para dejar de hacer tareas a mano." },
];

const MODULES = [
  ["Ventas", "Pedidos con reserva de stock, entrega y comprobante. Precios IVA incluido, descuentos y credito con limite por cliente."],
  ["Inventario", "Varios depositos, kardex inmutable, costo promedio ponderado y transferencias. Nunca stock negativo."],
  ["Compras", "Ordenes de compra, recepciones parciales y control de la factura del proveedor contra lo recibido."],
  ["Cobranzas y pagos", "Cobros, anticipos, antiguedad de saldos y extracto por cliente y proveedor."],
  ["Contabilidad", "Asientos automaticos, balance de comprobacion, estado de resultados y balance general."],
  ["Seguridad", "Base de datos separada por empresa, roles por funcion, auditoria y backups diarios cifrados."],
];

const SECTORS = ["Comercios y almacenes", "Talleres", "Consultorios", "Barberias y centros de belleza", "Distribuidoras", "Empresas de servicios"];

const PLANS = [
  {
    tag: "Mas elegido por PyMEs", name: "NEXATEC Cloud", price: "₲ 20.000 / dia", sub: "Plan mensual desde ₲ 550.000",
    text: "Sin comprar servidor. Accedes desde cualquier lugar, con backups, soporte mensual y actualizaciones.",
    items: ["ERP/CRM en la nube", "Acceso remoto", "Backups automaticos", "Soporte mensual incluido"],
    message: "Hola NEXATEC, quiero solicitar el plan NEXATEC Cloud (desde Gs. 550.000 al mes). ¿Podemos coordinar?",
    featured: true,
  },
  {
    tag: "Pago unico local", name: "NexaBox Local", price: "Desde ₲ 9.900.000", sub: "Sin mensualidad obligatoria",
    text: "Tu sistema funcionando dentro de tu comercio, con servidor local propio.",
    items: ["Servidor local instalado", "ERP/CRM configurado", "Backups locales", "Capacitacion inicial"],
    message: "Hola NEXATEC, quiero informacion del plan NexaBox Local de pago unico.",
  },
  {
    tag: "Exclusivo", name: "NEXATEC Enterprise", price: "A medida", sub: "Proyecto segun necesidad",
    text: "Para empresas que necesitan procesos propios, integraciones y BI avanzado.",
    items: ["ERP + CRM + BI a medida", "Integraciones", "Infraestructura dedicada", "Acuerdo de confidencialidad"],
    message: "Hola NEXATEC, quiero agendar una reunion por una solucion Enterprise a medida.",
  },
];

const FAQ = [
  ["¿Cuanto dura la demo y que incluye?", "14 dias, gratis. Te habilitamos una empresa de ejemplo con productos, clientes, stock, ventas, compras y contabilidad cargados para que pruebes todo el circuito."],
  ["¿Emite factura electronica (SIFEN)?", "Todavia no. Hoy los comprobantes del sistema son internos de gestion y llevan la leyenda \"sin validez tributaria\". La integracion con SIFEN se habilita recien cuando este homologada; no lo vamos a presentar como hecho antes."],
  ["¿Mis datos quedan mezclados con los de otras empresas?", "No. Cada empresa tiene su propia base de datos, con su propio usuario de acceso. Ademas hay backups diarios cifrados."],
  ["¿Puedo pasar de la demo a usar el sistema en serio?", "Si. Se crea tu sistema de produccion aparte, vacio, con tus datos reales. Los datos ficticios de la demo no se mezclan con los reales."],
  ["¿Como se contrata?", "Por WhatsApp o email: coordinamos una charla, elegis el plan y te dejamos el sistema listo. Hoy no cobramos con tarjeta en linea."],
];

export default function HomePage() {
  return (
    <PublicShell>
      <section className="mx-auto grid w-full max-w-[1180px] grid-cols-1 items-center gap-10 px-5 py-16 lg:grid-cols-[1.2fr_1fr] lg:py-24">
        <div>
          <p className="text-sm font-bold uppercase tracking-widest text-nx-accent">Hola, somos NEXATEC</p>
          <h1 className="mt-4 text-4xl font-extrabold leading-tight tracking-tight md:text-5xl">
            Transformamos tu PyME con tecnologia a medida
          </h1>
          <p className="mt-5 max-w-xl text-lg text-nx-muted">
            Ventas, stock, compras, cobranzas y contabilidad en un solo sistema, pensado para la operacion real de un negocio paraguayo.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Link href="/demo" className="inline-flex min-h-[48px] items-center rounded-full bg-gradient-to-b from-nx-accent to-nx-accent-2 px-6 font-bold text-[#06352f]">
              Probar demo gratis
            </Link>
            <WhatsAppButton variant="ghost" message="Hola NEXATEC, quiero agendar una consultoria gratuita para mi negocio.">
              Hablar por WhatsApp
            </WhatsAppButton>
          </div>
          <p className="mt-4 text-sm text-nx-muted">14 dias gratis · sin tarjeta · empresa de ejemplo precargada</p>
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
        <h2 className="text-3xl font-extrabold">Soluciones</h2>
        <p className="mt-2 max-w-2xl text-nx-muted">Para comercios, talleres, consultorios, centros de servicios y empresas que necesitan orden, control y crecer.</p>
        <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {SERVICES.map((s) => (
            <div key={s.title} className="rounded-2xl border border-nx-line bg-nx-card p-5">
              <h3 className="text-lg font-bold text-nx-accent">{s.title}</h3>
              <p className="mt-2 text-sm text-nx-muted">{s.text}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Que resuelve el ERP</h2>
        <div className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {MODULES.map(([t, d]) => (
            <div key={t} className="rounded-2xl border border-nx-line bg-black/10 p-5">
              <h3 className="font-bold">{t}</h3>
              <p className="mt-2 text-sm text-nx-muted">{d}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Como funciona la demo</h2>
        <ol className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3">
          {[["1", "Pedi tu demo", "Completas un formulario corto. Revisamos el pedido y te habilitamos."],
            ["2", "Proba 14 dias", "Entras con tu usuario a una empresa de ejemplo con todo cargado."],
            ["3", "Contrata cuando quieras", "Por WhatsApp te armamos tu sistema real, aparte de la demo."]].map(([n, t, d]) => (
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
        <h2 className="text-3xl font-extrabold">Planes</h2>
        <p className="mt-2 text-nx-muted">Elegi como queres empezar. Precios de referencia; te confirmamos la propuesta segun tu negocio.</p>
        <div className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
          {PLANS.map((p) => (
            <div key={p.name} className={`flex flex-col rounded-[24px] border p-6 ${p.featured ? "border-nx-accent bg-nx-card" : "border-nx-line bg-black/10"}`}>
              <p className="text-xs font-bold uppercase tracking-widest text-nx-accent">{p.tag}</p>
              <h3 className="mt-2 text-xl font-extrabold">{p.name}</h3>
              <p className="mt-3 text-2xl font-extrabold">{p.price}</p>
              <p className="text-sm text-nx-muted">{p.sub}</p>
              <p className="mt-3 text-sm text-nx-muted">{p.text}</p>
              <ul className="mt-4 flex-1 space-y-1 text-sm">
                {p.items.map((i) => <li key={i} className="flex items-center"><Check />{i}</li>)}
              </ul>
              <div className="mt-5"><WhatsAppButton variant={p.featured ? "primary" : "ghost"} message={p.message}>Consultar por WhatsApp</WhatsAppButton></div>
            </div>
          ))}
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Para que tipo de negocio</h2>
        <div className="mt-6 flex flex-wrap gap-2">
          {SECTORS.map((s) => <span key={s} className="rounded-full border border-nx-line px-4 py-2 text-sm">{s}</span>)}
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Quienes somos</h2>
        <p className="mt-4 max-w-3xl text-nx-muted">
          En NEXATEC ayudamos a transformar procesos manuales en sistemas simples, medibles y escalables. Combinamos desarrollo,
          operacion, automatizacion y analisis para que cada cliente pueda vender mejor, controlar mas y crecer con informacion confiable.
        </p>
      </section>

      <section id="preguntas" className="mx-auto w-full max-w-[1180px] px-5 py-14">
        <h2 className="text-3xl font-extrabold">Preguntas frecuentes</h2>
        <div className="mt-6 flex flex-col gap-3">
          {FAQ.map(([q, a]) => (
            <details key={q} className="rounded-2xl border border-nx-line bg-black/10 p-5">
              <summary className="cursor-pointer font-bold">{q}</summary>
              <p className="mt-3 text-sm text-nx-muted">{a}</p>
            </details>
          ))}
        </div>
      </section>

      <section className="mx-auto w-full max-w-[1180px] px-5 pb-20 pt-6">
        <div className="flex flex-col items-start justify-between gap-5 rounded-[28px] border border-nx-line bg-nx-card p-8 md:flex-row md:items-center">
          <div>
            <h2 className="text-2xl font-extrabold">Hablemos de tu negocio</h2>
            <p className="mt-1 text-nx-muted">Te contamos como quedaria el sistema con tus procesos.</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <WhatsAppButton message="Hola NEXATEC, quiero agendar una consultoria gratuita para mi negocio.">Escribir por WhatsApp</WhatsAppButton>
            <Link href="/demo" className="inline-flex min-h-[48px] items-center rounded-full border border-nx-line px-6 font-bold">Probar demo</Link>
          </div>
        </div>
      </section>
    </PublicShell>
  );
}
