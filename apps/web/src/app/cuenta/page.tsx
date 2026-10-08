"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { SecuritySection } from "@/components/security";
import { TopNav } from "@/components/ui";
import { api, ApiError, type CurrentUser } from "@/lib/api";

export default function CuentaPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const load = () =>
    api.me().then(setUser).catch((err) => {
      if (err instanceof ApiError && err.status === 401) router.replace("/login");
    });
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/portal" />
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-12">
        <h1 className="mb-6 text-3xl font-extrabold">Mi cuenta</h1>
        {user ? <SecuritySection user={user} onChange={load} /> : <p className="text-nx-muted">Cargando...</p>}
      </main>
    </div>
  );
}
