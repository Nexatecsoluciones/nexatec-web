"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ADMIN_ROLES, api, ApiError, type CurrentUser, type Role } from "@/lib/api";

export function useAdminGuard(requireRole?: Role) {
  const router = useRouter();
  const pathname = usePathname();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    api
      .me()
      .then((me) => {
        setUser(me);
        const allowed = requireRole ? me.role === requireRole : ADMIN_ROLES.includes(me.role);
        if (!allowed) setForbidden(true);
        // El servidor rechaza el Control Center sin MFA (MFA_REQUIRED); se
        // lleva directo a configurarlo en vez de mostrar pantallas con error.
        else if (me.mfa_required && !me.mfa_enabled && pathname !== "/admin/seguridad") router.replace("/admin/seguridad");
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else setForbidden(true);
      })
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function logout() {
    await api.logout().catch(() => undefined);
    router.replace("/login");
  }

  return { user, loading, forbidden, logout };
}
