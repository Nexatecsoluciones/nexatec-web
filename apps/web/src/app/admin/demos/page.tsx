"use client";

import { useEffect, useState } from "react";
import { AdminLayout, Badge, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, ApiError, type DemoRequestOut } from "@/lib/api";

export default function AdminDemosPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [requests, setRequests] = useState<DemoRequestOut[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  async function loadRequests() {
    setRequests(await api.listDemoRequests());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) loadRequests();
  }, [user]);

  async function onApprove(id: string) {
    setMessage(null);
    try {
      const result = await api.approveDemoRequest(id, 14);
      setMessage(
        `Demo ${result.job_status === "SUCCESS" ? "aprovisionada correctamente" : "con error de provisioning"}.` +
          (result.invite_token ? ` Token de invitación (STAGING ONLY): ${result.invite_token}` : ""),
      );
      await loadRequests();
    } catch (err) {
      setMessage(err instanceof ApiError ? err.message : "Error al aprobar.");
    }
  }

  async function onReject(id: string) {
    await api.rejectDemoRequest(id);
    await loadRequests();
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
    <AdminLayout activePath="/admin/demos" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Demo Center</h1>
      <p className="mt-1 text-nx-muted">Solicitudes de demo desde el catálogo público.</p>

      {message && <Card className="mt-6 p-4 text-sm text-nx-accent">{message}</Card>}

      <div className="mt-8 grid grid-cols-1 gap-4">
        {requests.length === 0 && <Card className="p-8 text-center text-nx-muted">Sin solicitudes todavía.</Card>}
        {requests.map((r) => (
          <Card key={r.id} className="flex flex-wrap items-center justify-between gap-4 p-6">
            <div>
              <Badge>{r.status}</Badge>
              <p className="mt-2 font-bold">{r.contact_name} {r.company_name ? `· ${r.company_name}` : ""}</p>
              <p className="text-xs text-nx-muted">{r.contact_email} {r.contact_phone ? `· ${r.contact_phone}` : ""}</p>
            </div>
            {(r.status === "NEW" || r.status === "CONTACTED") && (
              <div className="flex gap-3">
                <Button variant="secondary" onClick={() => onReject(r.id)}>Rechazar</Button>
                <Button onClick={() => onApprove(r.id)}>Aprobar y aprovisionar</Button>
              </div>
            )}
          </Card>
        ))}
      </div>
    </AdminLayout>
  );
}
