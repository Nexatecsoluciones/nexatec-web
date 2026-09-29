"use client";

import { useEffect, useState } from "react";
import { AdminLayout, Badge, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, type JobOut } from "@/lib/api";

const STATUS_COLOR: Record<string, string> = {
  SUCCESS: "text-nx-accent",
  FAILED: "text-red-300",
  RUNNING: "text-yellow-300",
  QUEUED: "text-nx-muted",
};

export default function AdminJobsPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [jobs, setJobs] = useState<JobOut[]>([]);

  useEffect(() => {
    if (user) api.listJobs().then(setJobs);
  }, [user]);

  if (loading) return <div className="flex flex-1 items-center justify-center text-nx-muted">Cargando...</div>;
  if (forbidden || !user) {
    return (
      <div className="flex flex-1 items-center justify-center p-10 text-center">
        <Card className="p-8"><h1 className="text-xl font-bold">No autorizado</h1></Card>
      </div>
    );
  }

  return (
    <AdminLayout activePath="/admin/jobs" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Provisioning Jobs</h1>
      <p className="mt-1 text-nx-muted">
        Se ejecutan de forma sincrónica hoy (sin cola/worker todavía) — los pasos son reales, no un porcentaje inventado.
      </p>

      <div className="mt-8 flex flex-col gap-4">
        {jobs.length === 0 && <Card className="p-8 text-center text-nx-muted">Sin jobs todavía.</Card>}
        {jobs.map((j) => (
          <Card key={j.id} className="p-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <Badge>{j.type}</Badge>
                <span className={`ml-3 text-sm font-bold ${STATUS_COLOR[j.status]}`}>{j.status}</span>
              </div>
              <span className="text-xs text-nx-muted">{new Date(j.created_at).toLocaleString("es-PY")}</span>
            </div>
            <ol className="mt-4 flex flex-col gap-1 text-sm">
              {j.steps.map((s, i) => (
                <li key={i} className={s.status === "failed" ? "text-red-300" : "text-nx-muted"}>
                  {s.status === "failed" ? "✗" : "✓"} {s.name} {s.detail ? `— ${s.detail}` : ""}
                </li>
              ))}
            </ol>
            {j.error_message && <p className="mt-2 text-sm text-red-300">{j.error_message}</p>}
          </Card>
        ))}
      </div>
    </AdminLayout>
  );
}
