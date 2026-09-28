"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Button, Card, TopNav } from "@/components/ui";
import { api, ApiError, type CurrentUser } from "@/lib/api";

export default function PortalPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .me()
      .then(setUser)
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

        <Card className="p-8 text-nx-muted">
          Todavia no tenes sistemas asignados. Cuando NEXATEC active una demo o un
          sistema en produccion para tu cuenta, va a aparecer aca.
        </Card>
      </main>
    </div>
  );
}
