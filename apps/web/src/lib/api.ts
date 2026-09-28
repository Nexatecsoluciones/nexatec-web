// Cliente HTTP minimo hacia la API. Nunca guarda secretos ni tokens: la
// sesion vive en una cookie HttpOnly que el navegador maneja solo, este
// codigo del lado cliente nunca la lee ni la escribe directamente.

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:4301";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
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
};
