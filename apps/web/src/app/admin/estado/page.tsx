"use client";

import { useEffect, useState } from "react";
import { AdminLayout, Badge, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, type HealthReport, type ServiceHealth } from "@/lib/api";

const STATUS_COLOR: Record<string, string> = {
  HEALTHY: "text-nx-accent",
  DEGRADED: "text-yellow-300",
  DOWN: "text-red-300",
  UNKNOWN: "text-nx-muted",
};

function ServiceRow({ s }: { s: ServiceHealth }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-nx-line/50 px-4 py-3 last:border-b-0">
      <div>
        <span className="font-semibold">{s.name}</span>
        <span className={`ml-3 text-xs font-bold ${STATUS_COLOR[s.status]}`}>{s.status}</span>
        {s.detail && <p className="mt-1 text-xs text-nx-muted">{s.detail}</p>}
      </div>
      <span className="text-xs text-nx-muted">{new Date(s.checked_at).toLocaleString("es-PY")}</span>
    </div>
  );
}

export default function AdminEstadoPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [report, setReport] = useState<HealthReport | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  async function loadHealth() {
    setReport(await api.getHealth());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) loadHealth();
  }, [user]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await loadHealth();
    } finally {
      setRefreshing(false);
    }
  }

  if (loading) return <div className="flex flex-1 items-center justify-center text-nx-muted">Cargando...</div>;
  if (forbidden || !user) {
    return (
      <div className="flex flex-1 items-center justify-center p-10 text-center">
        <Card className="p-8"><h1 className="text-xl font-bold">No autorizado</h1></Card>
      </div>
    );
  }

  return (
    <AdminLayout activePath="/admin/estado" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold">Health Center</h1>
          <p className="mt-1 text-nx-muted">Chequeos reales contra los servicios, no un estado inventado.</p>
        </div>
        <Button variant="secondary" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? "Actualizando..." : "Actualizar"}
        </Button>
      </div>

      <h2 className="mt-8 text-sm font-bold uppercase tracking-wide text-nx-muted">Infraestructura core</h2>
      <Card className="mt-3 overflow-hidden">
        {report && report.services.length > 0 ? (
          report.services.map((s) => <ServiceRow key={s.name} s={s} />)
        ) : (
          <p className="p-6 text-center text-nx-muted">Sin datos todavía.</p>
        )}
      </Card>

      <h2 className="mt-8 text-sm font-bold uppercase tracking-wide text-nx-muted">
        Service Registry <Badge>SUPER_ADMIN</Badge>
      </h2>
      <Card className="mt-3 overflow-hidden">
        {report && report.registered_services.length > 0 ? (
          report.registered_services.map((s) => <ServiceRow key={s.name} s={s} />)
        ) : (
          <p className="p-6 text-center text-nx-muted">Sin servicios registrados en el Service Registry.</p>
        )}
      </Card>
    </AdminLayout>
  );
}
