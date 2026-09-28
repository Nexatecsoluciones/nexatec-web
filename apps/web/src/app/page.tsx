import type { Metadata } from "next";
import Link from "next/link";
import { Badge, Card, TopNav } from "@/components/ui";
import type { SystemOut } from "@/lib/api";

export const metadata: Metadata = {
  title: "Soluciones",
  description: "Catalogo de soluciones NEXATEC: ERP, CRM, Business Intelligence y automatizacion.",
  robots: { index: true, follow: true },
};

export const dynamic = "force-dynamic";

async function getSystems(): Promise<SystemOut[]> {
  // Server Component: corre en el servidor de Next.js, nunca en el
  // navegador -- puede hablar directo con la API interna sin pasar por
  // el BFF de src/app/api/[...path]/route.ts (ese es solo para llamadas
  // que salen del navegador).
  const apiUrl = process.env.NEXATEC_INTERNAL_API_URL ?? "http://127.0.0.1:4301";
  try {
    const res = await fetch(`${apiUrl}/api/systems`, { cache: "no-store" });
    if (!res.ok) return [];
    return await res.json();
  } catch {
    return [];
  }
}

export default async function CatalogPage() {
  const systems = await getSystems();

  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/" />
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-16">
        <div className="mb-10 max-w-2xl">
          <Badge>Soluciones</Badge>
          <h1 className="mt-4 text-4xl font-extrabold tracking-tight">
            Catalogo de sistemas NEXATEC
          </h1>
          <p className="mt-3 text-nx-muted">
            Probá una demo o accedé a tu sistema si ya sos cliente.
          </p>
        </div>

        {systems.length === 0 ? (
          <Card className="p-8 text-nx-muted">
            No se pudo cargar el catalogo. Intentá de nuevo en unos minutos.
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {systems.map((system) => (
              <Card key={system.id} className="flex flex-col gap-4 p-6">
                <div>
                  <p className="text-xs font-bold uppercase tracking-widest text-nx-accent">
                    {system.category}
                  </p>
                  <h2 className="mt-2 text-xl font-bold">{system.name}</h2>
                </div>
                <p className="flex-1 text-sm leading-relaxed text-nx-muted">
                  {system.short_description}
                </p>
                <div className="flex gap-3">
                  {system.demo_available && (
                    <Link
                      href="/portal"
                      className="text-sm font-bold text-nx-accent hover:underline"
                    >
                      Probar demo
                    </Link>
                  )}
                  <Link href="/portal" className="text-sm font-bold text-nx-text hover:underline">
                    Acceder al sistema
                  </Link>
                </div>
              </Card>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
