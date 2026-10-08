// Contenido editable del sitio publico (CMS). Lo valida y guarda la API
// (app/services/site_content.py): solo texto plano, sin HTML ni URLs. Aca
// se renderiza siempre como texto (React escapa), nunca con
// dangerouslySetInnerHTML.

export interface Card { title: string; text: string }
export interface Plan {
  tag: string; name: string; price: string; sub: string; text: string; items: string[];
  whatsapp_message: string; featured: boolean;
}
export interface Faq { question: string; answer: string }

export interface HomeContent {
  announcement: string;
  hero: { eyebrow: string; title: string; subtitle: string; note: string; whatsapp_message: string };
  services_title: string; services_subtitle: string; services: Card[];
  modules_title: string; modules: Card[];
  plans_title: string; plans_subtitle: string; plans: Plan[];
  sectors_title: string; sectors: string[];
  about_title: string; about: string;
  faq_title: string; faqs: Faq[];
  cta: { title: string; subtitle: string; whatsapp_message: string };
}

const INTERNAL_API_URL = process.env.NEXATEC_INTERNAL_API_URL ?? "http://127.0.0.1:4301";

/** Solo en el servidor (Server Components). null si la API no responde: la
 *  pagina muestra una version minima en vez de romperse. */
export async function getPublishedHome(): Promise<HomeContent | null> {
  try {
    const res = await fetch(`${INTERNAL_API_URL}/api/public/site/home`, { cache: "no-store", signal: AbortSignal.timeout(3000) });
    if (!res.ok) return null;
    return (await res.json()) as HomeContent;
  } catch {
    return null;
  }
}
