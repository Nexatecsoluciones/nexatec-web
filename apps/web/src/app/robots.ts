import type { MetadataRoute } from "next";
import { isProduction, publicUrl } from "@/lib/site";

// Se calcula con el entorno del SERVIDOR (no el del build): un build hecho
// sin NEXATEC_ENV nunca puede dejar staging indexable ni produccion oculta.
export const dynamic = "force-dynamic";

// Staging nunca se indexa. En produccion solo las paginas publicas.
export default function robots(): MetadataRoute.Robots {
  if (!isProduction()) return { rules: { userAgent: "*", disallow: "/" } };
  return {
    rules: { userAgent: "*", allow: ["/", "/demo", "/privacidad", "/terminos", "/cookies"],
             disallow: ["/admin", "/portal", "/erp", "/imprimir", "/marcar", "/api", "/cuenta", "/login", "/restablecer", "/recuperar"] },
    sitemap: `${publicUrl()}/sitemap.xml`,
  };
}
