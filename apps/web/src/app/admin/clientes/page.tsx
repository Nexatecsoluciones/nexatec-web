"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AdminLayout, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, ApiError, type SystemOut, type TenantOut, type TenantUserOut } from "@/lib/api";

export default function AdminClientesPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();

  const [tenants, setTenants] = useState<TenantOut[]>([]);
  const [systems, setSystems] = useState<SystemOut[]>([]);
  const [selectedTenant, setSelectedTenant] = useState<TenantOut | null>(null);
  const [tenantUsers, setTenantUsers] = useState<TenantUserOut[]>([]);

  const [createTenantError, setCreateTenantError] = useState<string | null>(null);
  const [inviteResult, setInviteResult] = useState<string | null>(null);
  const [demoMessage, setDemoMessage] = useState<string | null>(null);

  async function loadTenants() {
    setTenants(await api.listTenants());
  }

  useEffect(() => {
    if (!user) return;
    // eslint-disable-next-line react-hooks/set-state-in-effect
    Promise.all([loadTenants(), api.listAllSystemsAdmin().then(setSystems)]);
  }, [user]);

  async function selectTenant(tenant: TenantOut) {
    setSelectedTenant(tenant);
    setInviteResult(null);
    setDemoMessage(null);
    setTenantUsers(await api.listTenantUsers(tenant.id));
  }

  async function onCreateTenant(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateTenantError(null);
    const form = new FormData(e.currentTarget);
    try {
      const tenant = await api.createTenant({
        slug: String(form.get("slug")),
        legal_name: String(form.get("legal_name")),
        display_name: String(form.get("display_name")),
        ruc: String(form.get("ruc") || "") || undefined,
        primary_email: String(form.get("primary_email") || "") || undefined,
        phone: String(form.get("phone") || "") || undefined,
        city: String(form.get("city") || "") || undefined,
        contact_name: String(form.get("contact_name") || "") || undefined,
        contact_email: String(form.get("contact_email") || "") || undefined,
        contact_phone: String(form.get("contact_phone") || "") || undefined,
      });
      e.currentTarget.reset();
      await loadTenants();
      await selectTenant(tenant);
    } catch (err) {
      setCreateTenantError(err instanceof ApiError ? err.message : "Error al crear el cliente.");
    }
  }

  async function onAssignUser(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selectedTenant) return;
    setInviteResult(null);
    const form = new FormData(e.currentTarget);
    try {
      const result = await api.assignTenantUser(selectedTenant.id, {
        email: String(form.get("email")),
        role: form.get("role") === "CLIENT_ADMIN" ? "CLIENT_ADMIN" : "CLIENT_USER",
        create_if_missing: true,
      });
      e.currentTarget.reset();
      setTenantUsers(await api.listTenantUsers(selectedTenant.id));
      setInviteResult(
        result.invite_token
          ? `Cuenta creada. Token de invitación (STAGING ONLY — compartir por canal seguro, no se puede recuperar después): ${result.invite_token}`
          : "Usuario asignado al cliente.",
      );
    } catch (err) {
      setInviteResult(err instanceof ApiError ? err.message : "Error al asignar el usuario.");
    }
  }

  async function onCreateDemo(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selectedTenant) return;
    setDemoMessage(null);
    const form = new FormData(e.currentTarget);
    try {
      const demo = await api.createDemo({
        tenant_id: selectedTenant.id,
        system_id: String(form.get("system_id")),
        duration_days: Number(form.get("duration_days") || 14),
      });
      setDemoMessage(`Demo aprovisionada y activada (estado: ${demo.status}).`);
    } catch (err) {
      setDemoMessage(err instanceof ApiError ? err.message : "Error al crear la demo.");
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
    <AdminLayout activePath="/admin/clientes" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Clientes</h1>
      <p className="mt-1 text-nx-muted">Tenants, usuarios y demos.</p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[0.9fr_1.1fr]">
        <div className="flex flex-col gap-6">
          <Card className="p-6">
            <h2 className="text-lg font-bold">Nuevo cliente</h2>
            <form onSubmit={onCreateTenant} className="mt-4 flex flex-col gap-3">
              <input name="slug" placeholder="slug (ej: acme)" required pattern="[a-z0-9-]+"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="legal_name" placeholder="Razón social" required minLength={2}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="display_name" placeholder="Nombre comercial" required minLength={2}
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="ruc" placeholder="RUC (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="primary_email" type="email" placeholder="Email principal (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="phone" placeholder="Teléfono (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="city" placeholder="Ciudad (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <p className="mt-1 text-xs font-bold uppercase tracking-wide text-nx-muted">Responsable</p>
              <input name="contact_name" placeholder="Nombre del responsable (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="contact_email" type="email" placeholder="Email del responsable (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              <input name="contact_phone" placeholder="Teléfono del responsable (opcional)"
                className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
              {createTenantError && <p className="text-sm font-semibold text-red-300">{createTenantError}</p>}
              <Button type="submit" className="w-full">Crear cliente</Button>
            </form>
          </Card>

          <Card className="overflow-hidden">
            <ul>
              {tenants.map((t) => (
                <li key={t.id}>
                  <button
                    onClick={() => selectTenant(t)}
                    className={`w-full px-5 py-3 text-left text-sm hover:bg-white/5 ${selectedTenant?.id === t.id ? "bg-white/10" : ""}`}
                  >
                    <span className="font-semibold">{t.display_name}</span>
                    <span className="ml-2 text-nx-muted">{t.status}</span>
                  </button>
                </li>
              ))}
              {tenants.length === 0 && (
                <li className="px-5 py-6 text-center text-sm text-nx-muted">Sin clientes todavía.</li>
              )}
            </ul>
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          {!selectedTenant ? (
            <Card className="p-8 text-nx-muted">Elegí un cliente de la lista para ver detalle.</Card>
          ) : (
            <>
              <Card className="p-6">
                <h2 className="text-lg font-bold">{selectedTenant.display_name}</h2>
                <p className="text-sm text-nx-muted">{selectedTenant.legal_name} · {selectedTenant.slug}</p>
                {(selectedTenant.ruc || selectedTenant.contact_name) && (
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-nx-muted">
                    {selectedTenant.ruc && <span>RUC: {selectedTenant.ruc}</span>}
                    {selectedTenant.phone && <span>Tel: {selectedTenant.phone}</span>}
                    {selectedTenant.contact_name && <span>Responsable: {selectedTenant.contact_name}</span>}
                    {selectedTenant.contact_email && <span>{selectedTenant.contact_email}</span>}
                  </div>
                )}

                <h3 className="mt-6 text-sm font-bold uppercase tracking-wide text-nx-muted">Usuarios</h3>
                <ul className="mt-2 flex flex-col gap-2">
                  {tenantUsers.map((tu) => (
                    <li key={tu.id} className="flex justify-between text-sm">
                      <span>{tu.email}</span>
                      <span className="text-nx-muted">{tu.role}</span>
                    </li>
                  ))}
                  {tenantUsers.length === 0 && <li className="text-sm text-nx-muted">Sin usuarios asignados.</li>}
                </ul>

                <form onSubmit={onAssignUser} className="mt-4 flex flex-wrap gap-2">
                  <input name="email" type="email" placeholder="email@cliente.com" required
                    className="flex-1 rounded-xl border border-nx-line bg-white/5 px-4 py-2 text-sm outline-none focus:border-nx-accent" />
                  <select name="role" className="rounded-xl border border-nx-line bg-white/5 px-3 py-2 text-sm">
                    <option value="CLIENT_USER">CLIENT_USER</option>
                    <option value="CLIENT_ADMIN">CLIENT_ADMIN</option>
                  </select>
                  <Button type="submit" variant="secondary">Asignar</Button>
                </form>
                {inviteResult && <p className="mt-2 break-all text-xs text-nx-accent">{inviteResult}</p>}
              </Card>

              <Card className="p-6">
                <h2 className="text-lg font-bold">Crear demo</h2>
                <form onSubmit={onCreateDemo} className="mt-4 flex flex-col gap-3">
                  <select name="system_id" required
                    className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm">
                    <option value="">Elegir sistema...</option>
                    {systems.filter((s) => s.demo_available).map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                  <input name="duration_days" type="number" defaultValue={14} min={1} max={90}
                    className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
                  <Button type="submit" className="w-full">Aprovisionar demo</Button>
                </form>
                {demoMessage && <p className="mt-2 text-sm text-nx-accent">{demoMessage}</p>}
              </Card>
            </>
          )}
        </div>
      </div>
    </AdminLayout>
  );
}
