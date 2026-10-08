import type { MetadataRoute } from "next";
import { isProduction, publicUrl } from "@/lib/site";

// Se calcula con el entorno del SERVIDOR (no el del build): un build hecho
// sin NEXATEC_ENV nunca puede dejar staging indexable ni produccion oculta.
export const dynamic = "force-dynamic";

export default function sitemap(): MetadataRoute.Sitemap {
  if (!isProduction()) return [];
  const base = publicUrl();
  return ["", "/demo", "/privacidad", "/terminos", "/cookies"].map((p) => ({
    url: `${base}${p}`, lastModified: new Date(), changeFrequency: p ? "monthly" : "weekly", priority: p ? 0.5 : 1,
  }));
}
