"use client";

import { useEffect, useState, type FormEvent } from "react";
import { AdminLayout, Badge, Button, Card } from "@/components/ui";
import { useAdminGuard } from "@/lib/useAdminGuard";
import { api, ApiError, type Role, type UserOut } from "@/lib/api";

const ROLES: Role[] = ["SUPER_ADMIN", "ADMIN", "SUPPORT", "BILLING", "CLIENT_ADMIN", "CLIENT_USER", "DEMO_USER"];
const GLOBAL_ROLES: Role[] = ["SUPER_ADMIN", "ADMIN", "SUPPORT", "BILLING"];

export default function AdminUsuariosPage() {
  const { user, loading, forbidden, logout } = useAdminGuard();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [createError, setCreateError] = useState<string | null>(null);
  const [inviteResult, setInviteResult] = useState<string | null>(null);

  async function loadUsers() {
    setUsers(await api.listUsers());
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (user) loadUsers();
  }, [user]);

  async function onCreate(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setCreateError(null);
    const form = new FormData(e.currentTarget);
    const role = String(form.get("role")) as Role;
    const tenantId = String(form.get("tenant_id") || "").trim();
    try {
      const result = await api.createUser({
        email: String(form.get("email")),
        full_name: String(form.get("full_name")),
        role,
        tenant_id: GLOBAL_ROLES.includes(role) ? null : tenantId || null,
      });
      e.currentTarget.reset();
      await loadUsers();
      setInviteResult(`Cuenta creada. Token de invitación (STAGING ONLY, compartir por canal seguro): ${result.invite_token}`);
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Error al crear el usuario.");
    }
  }

  async function onToggle(u: UserOut) {
    if (u.is_active) await api.disableUser(u.id);
    else await api.reactivateUser(u.id);
    await loadUsers();
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
    <AdminLayout activePath="/admin/usuarios" userEmail={user.email} userRole={user.role} onLogout={logout}>
      <h1 className="text-3xl font-extrabold">Usuarios</h1>
      <p className="mt-1 text-nx-muted">Crear, invitar, deshabilitar y reactivar usuarios.</p>

      <div className="mt-8 grid grid-cols-1 gap-8 lg:grid-cols-[1.2fr_0.8fr]">
        <Card className="overflow-hidden">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-nx-line text-nx-muted">
              <tr>
                <th className="px-4 py-3">Email</th>
                <th className="px-4 py-3">Rol</th>
                <th className="px-4 py-3">Estado</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-b border-nx-line/50">
                  <td className="px-4 py-2.5">{u.email}</td>
                  <td className="px-4 py-2.5"><Badge>{u.role}</Badge></td>
                  <td className="px-4 py-2.5">{u.is_active ? "Activo" : "Deshabilitado"}</td>
                  <td className="px-4 py-2.5 text-right">
                    <Button variant="secondary" className="min-h-[32px] px-3 text-xs" onClick={() => onToggle(u)}>
                      {u.is_active ? "Deshabilitar" : "Reactivar"}
                    </Button>
                  </td>
                </tr>
              ))}
              {users.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-6 text-center text-nx-muted">Sin usuarios todavía.</td></tr>
              )}
            </tbody>
          </table>
        </Card>

        <Card className="p-6">
          <h2 className="text-lg font-bold">Nuevo usuario</h2>
          <form onSubmit={onCreate} className="mt-4 flex flex-col gap-3">
            <input name="full_name" placeholder="Nombre completo" required
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            <input name="email" type="email" placeholder="Email" required
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            <select name="role" className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm">
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input name="tenant_id" placeholder="tenant_id (solo si es CLIENT_*)"
              className="rounded-xl border border-nx-line bg-white/5 px-4 py-2.5 text-sm outline-none focus:border-nx-accent" />
            {createError && <p className="text-sm font-semibold text-red-300">{createError}</p>}
            <Button type="submit" className="w-full">Crear e invitar</Button>
          </form>
          {inviteResult && <p className="mt-3 break-all text-xs text-nx-accent">{inviteResult}</p>}
        </Card>
      </div>
    </AdminLayout>
  );
}
