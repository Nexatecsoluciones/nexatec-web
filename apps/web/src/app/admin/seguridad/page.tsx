"use client";

import { useState } from "react";
import { SecuritySection } from "@/components/security";
import { AdminLayout } from "@/components/ui";
import { api, type CurrentUser } from "@/lib/api";
import { useAdminGuard } from "@/lib/useAdminGuard";

export default function AdminSeguridadPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [fresh, setFresh] = useState<CurrentUser | null>(null);
  if (loading) return <main className="flex-1 py-20 text-center text-nx-muted">Cargando...</main>;
  if (forbidden || !user) return <main className="flex-1 py-20 text-center">No autorizado.</main>;
  const current = fresh ?? user;
  return (
    <AdminLayout activePath="/admin/seguridad" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="mb-2 text-2xl font-extrabold">Seguridad de la cuenta</h1>
      {current.mfa_required && !current.mfa_enabled && (
        <p className="mb-5 rounded-xl border border-amber-300/40 bg-amber-400/10 p-3 text-sm text-amber-100">
          Para usar el Control Center primero tenes que activar la verificacion en dos pasos.
        </p>
      )}
      <SecuritySection user={current} onChange={() => api.me().then(setFresh)} />
    </AdminLayout>
  );
}
