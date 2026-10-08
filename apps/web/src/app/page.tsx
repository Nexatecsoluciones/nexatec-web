import type { Metadata } from "next";
import { Announcement, HomeBody, HomeFallback } from "@/components/home";
import { PublicShell } from "@/components/site";
import { isProduction } from "@/lib/site";
import { getPublishedHome } from "@/lib/site-content";

// El meta robots depende del entorno del servidor (staging nunca indexable)
// y el contenido publicado del CMS se lee en cada request.
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

export default async function HomePage() {
  const content = await getPublishedHome();
  return (
    <>
      {content && <Announcement text={content.announcement} />}
      <PublicShell>{content ? <HomeBody c={content} /> : <HomeFallback />}</PublicShell>
    </>
  );
}
