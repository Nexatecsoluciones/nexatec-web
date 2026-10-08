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
  mfa_enabled: boolean;
  mfa_required: boolean;
}

// Etapa de la sesion tras el login: FULL = lista para usar; MFA o
// PASSWORD_CHANGE = restringida hasta completar ese paso.
export type LoginStage = "FULL" | "MFA" | "PASSWORD_CHANGE";

export interface ConfigFieldDef {
  key: string;
  label: string;
  type: "text" | "select" | "boolean" | "number" | "media" | "secret";
  required?: boolean;
  options?: string[];
  default?: unknown;
}

export interface ModuleDef {
  key: string;
  label: string;
  default_enabled?: boolean;
}

export type ImplementationStatus = "PENDING" | "PARTIAL" | "READY";

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
  is_public: boolean;
  sort_order: number;
  implementation_status: ImplementationStatus;
  icon: string | null;
  image_url: string | null;
  video_url: string | null;
  default_demo_duration_days: number;
  default_max_users: number | null;
  default_storage_mb: number | null;
  config_schema: ConfigFieldDef[] | null;
  modules_schema: ModuleDef[] | null;
}

export type TenantStatus = "ACTIVE" | "SUSPENDED" | "ARCHIVED";
export type TenantMemberRole =
  | "CLIENT_ADMIN"
  | "CLIENT_USER"
  | "MANAGER"
  | "FINANCE"
  | "ACCOUNTANT"
  | "SALES"
  | "PURCHASING"
  | "WAREHOUSE"
  | "AUDITOR";
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
  ruc: string | null;
  primary_email: string | null;
  phone: string | null;
  country: string | null;
  city: string | null;
  contact_name: string | null;
  contact_email: string | null;
  contact_phone: string | null;
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
    request<{ email: string; role: Role; next: LoginStage }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password, turnstile_token: turnstileToken }),
    }),
  requestPasswordReset: (email: string, turnstileToken: string) =>
    request<{ ok: boolean }>("/api/auth/password-reset/request", {
      method: "POST",
      body: JSON.stringify({ email, turnstile_token: turnstileToken }),
    }),
  confirmPasswordReset: (token: string, newPassword: string) =>
    request<{ ok: boolean }>("/api/auth/password-reset/confirm", {
      method: "POST",
      body: JSON.stringify({ token, new_password: newPassword }),
    }),
  mfaVerify: (code: string) =>
    request<{ next: LoginStage }>("/api/auth/mfa/verify", { method: "POST", body: JSON.stringify({ code }) }),
  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ next: LoginStage }>("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  mfaSetup: () => request<{ secret: string; otpauth_uri: string }>("/api/auth/mfa/setup", { method: "POST" }),
  mfaEnable: (code: string) =>
    request<{ mfa_enabled: boolean }>("/api/auth/mfa/enable", { method: "POST", body: JSON.stringify({ code }) }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  listPublicSystems: () => request<SystemOut[]>("/api/systems"),
  listAllSystemsAdmin: () => request<SystemOut[]>("/api/admin/systems"),
  createSystemAdmin: (data: Partial<SystemOut> & { slug: string; name: string; short_description: string; category: string }) =>
    request<SystemOut>("/api/admin/systems", {
      method: "POST",
      body: JSON.stringify(data),
    }),
  updateSystemAdmin: (systemId: string, data: Partial<SystemOut>) =>
    request<SystemOut>(`/api/admin/systems/${systemId}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),
  getSystemAdmin: (systemId: string) => request<SystemOut>(`/api/admin/systems/${systemId}`),

  // --- Portal (cliente autenticado) ---
  myUsers: () => request<MySystemOut[]>("/api/portal/my-systems"),
  requestAccess: (systemAccessId: string) =>
    request<{ status: string; message: string }>(
      `/api/portal/my-systems/${systemAccessId}/access`,
      { method: "POST" },
    ),

  // --- Admin: tenants ---
  listTenants: () => request<TenantOut[]>("/api/admin/tenants"),
  createTenant: (data: {
    slug: string;
    legal_name: string;
    display_name: string;
    ruc?: string;
    primary_email?: string;
    phone?: string;
    city?: string;
    contact_name?: string;
    contact_email?: string;
    contact_phone?: string;
  }) =>
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

  // --- Admin: pagos ---
  listPaymentOrdersAdmin: (statusFilter?: string) =>
    request<PaymentOrderOut[]>(
      `/api/admin/payment-orders${statusFilter ? `?status_filter=${statusFilter}` : ""}`,
    ),
  approvePaymentOrder: (orderId: string, note?: string) =>
    request<PaymentOrderOut>(`/api/admin/payment-orders/${orderId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),
  rejectPaymentOrder: (orderId: string, note?: string) =>
    request<PaymentOrderOut>(`/api/admin/payment-orders/${orderId}/reject`, {
      method: "POST",
      body: JSON.stringify({ note }),
    }),

  // --- Bootstrap ---
  bootstrapStatus: () => request<{ initialized: boolean }>("/api/system/bootstrap-status"),

  // --- Dashboard ---
  getDashboard: () => request<DashboardStats>("/api/admin/dashboard"),

  // --- Health ---
  getHealth: () => request<HealthReport>("/api/admin/health"),

  // --- Jobs ---
  listJobs: (statusFilter?: string) =>
    request<JobOut[]>(`/api/admin/jobs${statusFilter ? `?status_filter=${statusFilter}` : ""}`),

  // --- Usuarios ---
  listUsers: (params?: { tenant_id?: string; role?: Role }) => {
    const qs = new URLSearchParams(params as Record<string, string>).toString();
    return request<UserOut[]>(`/api/admin/users${qs ? `?${qs}` : ""}`);
  },
  createUser: (data: { email: string; full_name: string; role: Role; tenant_id?: string | null }) =>
    request<{ user: UserOut; invite_token: string }>("/api/admin/users", { method: "POST", body: JSON.stringify(data) }),
  disableUser: (userId: string) => request<UserOut>(`/api/admin/users/${userId}/disable`, { method: "POST" }),
  reactivateUser: (userId: string) => request<UserOut>(`/api/admin/users/${userId}/reactivate`, { method: "POST" }),

  // --- Demo requests ---
  listDemoRequests: (statusFilter?: string) =>
    request<DemoRequestOut[]>(`/api/admin/demo-requests${statusFilter ? `?status_filter=${statusFilter}` : ""}`),
  approveDemoRequest: (requestId: string, durationDays: number) =>
    request<{ demo_request: DemoRequestOut; tenant_id: string; invite_token: string | null; job_status: string }>(
      `/api/admin/demo-requests/${requestId}/approve`,
      { method: "POST", body: JSON.stringify({ duration_days: durationDays }) },
    ),
  rejectDemoRequest: (requestId: string) =>
    request<DemoRequestOut>(`/api/admin/demo-requests/${requestId}/reject`, { method: "POST", body: JSON.stringify({}) }),

  // --- Plans ---
  listPlans: () => request<PlanOut[]>("/api/plans"),
  createPlan: (data: { slug: string; name: string; price_amount: string; price_currency?: string; billing_period: string }) =>
    request<PlanOut>("/api/admin/plans", { method: "POST", body: JSON.stringify(data) }),
};

export interface DashboardStats {
  active_tenants: number;
  total_users: number;
  active_demos: number;
  demos_expiring_soon: number;
  active_productions: number;
  deployed_systems: number;
  storage_used_mb: number;
  pending_payments: number;
  recent_tenants: { id: string; display_name: string; created_at: string }[];
  recent_demos: { id: string; tenant_id: string; status: string; created_at: string }[];
  recent_payments: { id: string; amount: string; currency: string; status: string; created_at: string }[];
}

export interface ServiceHealth {
  name: string;
  status: "HEALTHY" | "DEGRADED" | "DOWN" | "UNKNOWN";
  detail: string | null;
  checked_at: string;
}

export interface HealthReport {
  services: ServiceHealth[];
  registered_services: ServiceHealth[];
}

export interface JobOut {
  id: string;
  type: string;
  tenant_id: string;
  system_id: string;
  environment: Environment;
  status: "QUEUED" | "RUNNING" | "SUCCESS" | "FAILED";
  steps: { name: string; status: string; detail: string | null; at: string }[];
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface UserOut {
  id: string;
  email: string;
  full_name: string | null;
  role: Role;
  tenant_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface DemoRequestOut {
  id: string;
  contact_name: string;
  contact_email: string;
  contact_phone: string | null;
  company_name: string | null;
  system_id: string | null;
  message: string | null;
  status: "NEW" | "CONTACTED" | "APPROVED" | "REJECTED" | "PROVISIONED";
  created_at: string;
}

export interface PlanOut {
  id: string;
  slug: string;
  name: string;
  price_amount: string;
  price_currency: string;
  billing_period: string;
}

export interface PaymentOrderOut {
  id: string;
  tenant_id: string;
  plan_id: string;
  method: "BANK_TRANSFER" | "BANCARD_CARD";
  status: string;
  amount: string;
  currency: string;
  created_at: string;
}
