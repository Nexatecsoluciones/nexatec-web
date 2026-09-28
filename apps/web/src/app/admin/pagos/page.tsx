"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Badge, Button, Card, TopNav } from "@/components/ui";
import { ADMIN_ROLES, api, ApiError, type CurrentUser, type PaymentOrderOut } from "@/lib/api";

function formatMoney(amount: string, currency: string): string {
  const n = Number(amount);
  return `${currency} ${n.toLocaleString("es-PY")}`;
}

export default function AdminPagosPage() {
  const router = useRouter();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [forbidden, setForbidden] = useState(false);
  const [loading, setLoading] = useState(true);
  const [orders, setOrders] = useState<PaymentOrderOut[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);

  async function loadOrders() {
    setOrders(await api.listPaymentOrdersAdmin("UNDER_REVIEW"));
  }

  useEffect(() => {
    api
      .me()
      .then(async (me) => {
        setUser(me);
        if (!ADMIN_ROLES.includes(me.role)) {
          setForbidden(true);
          return;
        }
        await loadOrders();
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
        else if (err instanceof ApiError && err.status === 403) setForbidden(true);
      })
      .finally(() => setLoading(false));
  }, [router]);

  async function onApprove(orderId: string) {
    setActionError(null);
    try {
      await api.approvePaymentOrder(orderId);
      await loadOrders();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Error al aprobar.");
    }
  }

  async function onReject(orderId: string) {
    setActionError(null);
    try {
      await api.rejectPaymentOrder(orderId);
      await loadOrders();
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Error al rechazar.");
    }
  }

  if (loading) {
    return (
      <div className="flex flex-1 flex-col">
        <TopNav activePath="/admin" />
        <main className="flex-1 px-5 py-16 text-center text-nx-muted">Cargando...</main>
      </div>
    );
  }

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
    <div className="flex flex-1 flex-col">
      <TopNav activePath="/admin" />
      <main className="mx-auto w-full max-w-[1180px] flex-1 px-5 py-16">
        <h1 className="text-3xl font-extrabold">Pagos</h1>
        <p className="mt-1 text-nx-muted">Transferencias bancarias pendientes de revisión.</p>

        {actionError && <Card className="mt-6 p-4 text-sm text-red-300">{actionError}</Card>}

        <div className="mt-8 grid grid-cols-1 gap-4">
          {orders.length === 0 && (
            <Card className="p-8 text-center text-nx-muted">No hay comprobantes pendientes de revisión.</Card>
          )}
          {orders.map((o) => (
            <Card key={o.id} className="flex flex-wrap items-center justify-between gap-4 p-6">
              <div>
                <Badge>{o.method === "BANK_TRANSFER" ? "Transferencia" : "Tarjeta"}</Badge>
                <p className="mt-2 text-lg font-bold">{formatMoney(o.amount, o.currency)}</p>
                <p className="text-xs text-nx-muted">
                  Orden {o.id.slice(0, 8)} · {new Date(o.created_at).toLocaleString("es-PY")}
                </p>
              </div>
              <div className="flex gap-3">
                <Button variant="secondary" onClick={() => onReject(o.id)}>
                  Rechazar
                </Button>
                <Button onClick={() => onApprove(o.id)}>Aprobar</Button>
              </div>
            </Card>
          ))}
        </div>
      </main>
    </div>
  );
}
