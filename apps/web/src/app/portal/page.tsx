"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge, Button, Card, TopNav } from "@/components/ui";
import { api, ApiError, type CurrentUser, type MySystemOut } from "@/lib/api";

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: "Activo",
  SUSPENDED: "Suspendido",
  EXPIRED: "Expirado",
};

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleDateString("es-PY");
}

export default function PortalPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [systems, setSystems] = useState<MySystemOut[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(async (me) => {
        setUser(me);
        const mine = await api.myUsers();
        setSystems(mine);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        }
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function onLogout() {
    await api.logout().catch(() => undefined);
    router.replace("/login");
  }

  async function onAccess(systemAccessId: string) {
    setActionMessage(null);
    try {
      // El servidor decide si corresponde (membresia, acceso activo, no
      // vencido); recien entonces se abre el sistema.
      await api.requestAccess(systemAccessId);
      router.push(`/erp/${systemAccessId}`);
    } catch (err) {
      setActionMessage(err instanceof ApiError ? err.message : "No se pudo procesar el acceso.");
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 flex-col">
        <TopNav activePath="/portal" />
        <main className="flex-1 px-5 py-16 text-center text-nx-muted">Cargando...</main>
      </div>
    );
  }

  if (!user) return null;

  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/portal" />
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-16">
        <div className="mb-10 flex flex-wrap items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-extrabold">Mis sistemas</h1>
            <p className="mt-1 text-nx-muted">{user.email}</p>
          </div>
          <Button variant="secondary" onClick={onLogout}>
            Cerrar sesion
          </Button>
        </div>

        {actionMessage && (
          <Card className="mb-6 p-4 text-sm text-nx-accent">{actionMessage}</Card>
        )}

        {systems.length === 0 ? (
          <Card className="p-8 text-nx-muted">
            Todavia no tenes sistemas asignados. Cuando NEXATEC active una demo o un
            sistema en produccion para tu cuenta, va a aparecer aca.
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {systems.map((s) => (
              <Card key={s.system_access_id} className="flex flex-col gap-3 p-6">
                <Badge>{s.environment === "DEMO" ? "Demo" : "Produccion"}</Badge>
                <h2 className="text-xl font-bold">{s.system_name}</h2>
                <p className="text-sm text-nx-muted">
                  {STATUS_LABEL[s.status] ?? s.status}
                  {s.expires_at && ` · Expira ${formatDate(s.expires_at)}`}
                </p>
                <Button
                  variant={s.status === "ACTIVE" ? "primary" : "secondary"}
                  disabled={s.status !== "ACTIVE"}
                  onClick={() => onAccess(s.system_access_id)}
                  className="mt-auto w-full"
                >
                  {s.environment === "DEMO" ? "Probar demo" : "Acceder"}
                </Button>
              </Card>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
