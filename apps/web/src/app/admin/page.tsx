"use client";

import { useEffect, useState } from "react";
import { AdminLayout, Card, TopNav } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, ApiError, type DashboardStats } from "@/lib/api";

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <Card className="p-5">
      <p className="text-xs font-bold uppercase tracking-wide text-nx-muted">{label}</p>
      <p className="mt-2 text-3xl font-extrabold">{value}</p>
    </Card>
  );
}

function NotInitialized() {
  return (
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/admin" />
      <main className="mx-auto w-full max-w-lg flex-1 px-5 py-24 text-center">
        <Card className="p-10">
          <h1 className="text-xl font-bold">Plataforma aún no inicializada</h1>
          <p className="mt-3 text-sm text-nx-muted">
            Todavía no existe un SUPER_ADMIN. La creación solo puede hacerse desde
            la terminal del servidor (CLI), nunca desde esta pantalla.
          </p>
        </Card>
      </main>
    </div>
  );
}

export default function AdminDashboardPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [notInitialized, setNotInitialized] = useState(false);
  const [statsError, setStatsError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api
      .getDashboard()
      .then(setStats)
      .catch((err) => setStatsError(err instanceof ApiError ? err.message : "Error al cargar el dashboard."));
  }, [user]);

  useEffect(() => {
    if (forbidden) {
      api.bootstrapStatus().then((s) => {
        if (!s.initialized) setNotInitialized(true);
      });
    }
  }, [forbidden]);

  if (loading) return <div className="flex flex-1 items-center justify-center text-nx-muted">Cargando...</div>;

  if (notInitialized) return <NotInitialized />;

  if (forbidden || !user) {
    return (
      <div className="flex flex-1 flex-col">
        <TopNav activePath="/admin" />
        <main className="mx-auto w-full max-w-lg flex-1 px-5 py-16 text-center">
          <Card className="p-8">
            <h1 className="text-xl font-bold">No autorizado</h1>
          </Card>
        </main>
      </div>
    );
  }

  return (
    <AdminLayout activePath="/admin" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Dashboard</h1>
      <p className="mt-1 text-nx-muted">Estado operativo real de NEXATEC.</p>

      {statsError && <Card className="mt-6 p-4 text-sm text-red-300">{statsError}</Card>}

      {!stats ? (
        <p className="mt-8 text-nx-muted">Cargando métricas...</p>
      ) : (
        <>
          <div className="mt-8 grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Clientes activos" value={stats.active_tenants} />
            <StatCard label="Usuarios" value={stats.total_users} />
            <StatCard label="Demos activas" value={stats.active_demos} />
            <StatCard label="Demos por vencer (7d)" value={stats.demos_expiring_soon} />
            <StatCard label="Producciones activas" value={stats.active_productions} />
            <StatCard label="Sistemas desplegados" value={stats.deployed_systems} />
            <StatCard label="Storage usado" value={`${stats.storage_used_mb} MB`} />
            <StatCard label="Pagos pendientes" value={stats.pending_payments} />
          </div>

          <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <Card className="p-5">
              <h2 className="mb-3 text-sm font-bold uppercase text-nx-muted">Últimos clientes</h2>
              {stats.recent_tenants.length === 0 && <p className="text-sm text-nx-muted">Sin clientes todavía.</p>}
              <ul className="flex flex-col gap-2 text-sm">
                {stats.recent_tenants.map((t) => (
                  <li key={t.id} className="flex justify-between">
                    <span>{t.display_name}</span>
                    <span className="text-nx-muted">{new Date(t.created_at).toLocaleDateString("es-PY")}</span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="p-5">
              <h2 className="mb-3 text-sm font-bold uppercase text-nx-muted">Últimas demos</h2>
              {stats.recent_demos.length === 0 && <p className="text-sm text-nx-muted">Sin demos todavía.</p>}
              <ul className="flex flex-col gap-2 text-sm">
                {stats.recent_demos.map((d) => (
                  <li key={d.id} className="flex justify-between">
                    <span>{d.status}</span>
                    <span className="text-nx-muted">{new Date(d.created_at).toLocaleDateString("es-PY")}</span>
                  </li>
                ))}
              </ul>
            </Card>
            <Card className="p-5">
              <h2 className="mb-3 text-sm font-bold uppercase text-nx-muted">Últimos pagos</h2>
              {stats.recent_payments.length === 0 && <p className="text-sm text-nx-muted">Sin pagos todavía.</p>}
              <ul className="flex flex-col gap-2 text-sm">
                {stats.recent_payments.map((p) => (
                  <li key={p.id} className="flex justify-between">
                    <span>{p.currency} {Number(p.amount).toLocaleString("es-PY")}</span>
                    <span className="text-nx-muted">{p.status}</span>
                  </li>
                ))}
              </ul>
            </Card>
          </div>
        </>
      )}
    </AdminLayout>
  );
}
