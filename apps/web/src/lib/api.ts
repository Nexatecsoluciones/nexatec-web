// Cliente HTTP minimo hacia la API. Same-origin: siempre pega a rutas
// relativas /api/* de este mismo Next.js (ver
// src/app/api/[...path]/route.ts), que reenvia server-side hacia la API
// interna. El navegador nunca conoce el host/puerto real de FastAPI.
// Nunca guarda secretos ni tokens: la sesion vive en una cookie HttpOnly
// que el navegador maneja solo, este codigo del lado cliente nunca la lee
// ni la escribe directamente.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      // respuesta sin JSON, mantener statusText
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export type Role =
  | "SUPER_ADMIN"
  | "ADMIN"
  | "SUPPORT"
  | "BILLING"
  | "CLIENT_ADMIN"
  | "CLIENT_USER"
  | "DEMO_USER";

export const ADMIN_ROLES: Role[] = ["SUPER_ADMIN", "ADMIN", "SUPPORT", "BILLING"];

export interface CurrentUser {
  id: string;
  email: string;
  role: Role;
  tenant_id: string | null;
}

export interface SystemOut {
  id: string;
  slug: string;
  name: string;
  short_description: string;
  description: string | null;
  category: string;
  demo_available: boolean;
  production_available: boolean;
  is_active: boolean;
  sort_order: number;
}

export type TenantStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";
export type TenantMemberRole = "CLIENT_ADMIN" | "CLIENT_USER";
export type Environment = "DEMO" | "PRODUCTION";
export type ProvisioningStatus =
  | "REQUESTED"
  | "PROVISIONING"
  | "READY"
  | "FAILED"
  | "DEPROVISIONING"
  | "DEPROVISIONED";

export interface TenantOut {
  id: string;
  slug: string;
  legal_name: string;
  display_name: string;
  status: TenantStatus;
  created_at: string;
}

export interface TenantUserOut {
  id: string;
  tenant_id: string;
  user_id: string;
  email: string;
  role: TenantMemberRole;
  status: string;
  invite_token: string | null;
}

export interface DemoInstanceOut {
  id: string;
  tenant_id: string;
  system_id: string;
  system_access_id: string;
  status: ProvisioningStatus;
  starts_at: string | null;
  expires_at: string | null;
}

export interface MySystemOut {
  system_access_id: string;
  system_slug: string;
  system_name: string;
  environment: Environment;
  status: string;
  expires_at: string | null;
}

export const api = {
  me: () => request<CurrentUser>("/api/auth/me"),
  login: (email: string, password: string, turnstileToken: string) =>
    request<{ email: string; role: Role }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, turnstile_token: turnstileToken }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  listPublicSystems: () => request<SystemOut[]>("/api/systems"),
  listAllSystemsAdmin: () => request<SystemOut[]>("/api/admin/systems"),
  createSystemAdmin: (data: {
    slug: string;
    name: string;
    short_description: string;
    category: string;
    demo_available?: boolean;
    production_available?: boolean;
  }) =>
    request<SystemOut>("/api/admin/systems", {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // --- Portal (cliente autenticado) ---
  myUsers: () => request<MySystemOut[]>("/api/portal/my-systems"),
  requestAccess: (systemAccessId: string) =>
    request<{ status: string; message: string }>(
      `/api/portal/my-systems/${systemAccessId}/access`,
      { method: "POST" },
    ),

  // --- Admin: tenants ---
  listTenants: () => request<TenantOut[]>("/api/admin/tenants"),
  createTenant: (data: { slug: string; legal_name: string; display_name: string }) =>
    request<TenantOut>("/api/admin/tenants", { method: "POST", body: JSON.stringify(data) }),
  listTenantUsers: (tenantId: string) =>
    request<TenantUserOut[]>(`/api/admin/tenants/${tenantId}/users`),
  assignTenantUser: (
    tenantId: string,
    data: { email: string; role: TenantMemberRole; create_if_missing?: boolean },
  ) =>
    request<TenantUserOut>(`/api/admin/tenants/${tenantId}/users`, {
      method: "POST",
      body: JSON.stringify(data),
    }),

  // --- Admin: demos ---
  listDemos: () => request<DemoInstanceOut[]>("/api/admin/demos"),
  createDemo: (data: { tenant_id: string; system_id: string; duration_days?: number }) =>
    request<DemoInstanceOut>("/api/admin/demos", { method: "POST", body: JSON.stringify(data) }),
  renewDemo: (demoId: string, extraDays: number) =>
    request<DemoInstanceOut>(`/api/admin/demos/${demoId}/renew`, {
      method: "POST",
      body: JSON.stringify({ extra_days: extraDays }),
    }),
  suspendDemo: (demoId: string) =>
    request<DemoInstanceOut>(`/api/admin/demos/${demoId}/suspend`, { method: "POST" }),
};
