"""Matriz de permisos del ERP por rol dentro de la empresa.

Un permiso es "<modulo>:<accion>". Esta es la UNICA fuente de verdad: los
endpoints la consultan (via erp_context.require) y la interfaz recibe la
lista resultante solo para decidir que mostrar -- el servidor siempre
revalida.

Modulos: settings (empresa, sucursales, depositos), products, parties,
inventory, sales, receivables (facturas internas, cobros), purchases,
payables (facturas de proveedor, pagos), accounting, dashboard."""

from app.models.tenancy_enums import TenantMemberRole as R

MODULES = ("settings", "products", "parties", "inventory", "sales", "receivables",
           "purchases", "payables", "accounting", "dashboard")

ALL_READ = {f"{m}:read" for m in MODULES}
ALL_WRITE = {f"{m}:write" for m in MODULES if m != "dashboard"}
SPECIAL = {"sales:deliver", "purchases:receive", "accounting:periods"}

PERMISSIONS: dict[R, frozenset[str]] = {
    R.CLIENT_ADMIN: frozenset(ALL_READ | ALL_WRITE | SPECIAL),
    # Solo lectura de todo (rol historico "usuario").
    R.CLIENT_USER: frozenset(ALL_READ),
    R.AUDITOR: frozenset(ALL_READ),
    R.MANAGER: frozenset(ALL_READ | {"products:write", "parties:write", "sales:write", "purchases:write"}),
    R.FINANCE: frozenset(ALL_READ | {"parties:write", "receivables:write", "payables:write",
                                     "accounting:write", "accounting:periods"}),
    R.ACCOUNTANT: frozenset(ALL_READ | {"accounting:write", "accounting:periods"}),
    # Ventas: no ve contabilidad, ni proveedores/pagos, ni el tablero de montos.
    R.SALES: frozenset({"settings:read", "products:read", "parties:read", "inventory:read", "sales:read",
                        "receivables:read", "parties:write", "sales:write", "sales:deliver"}),
    R.PURCHASING: frozenset({"settings:read", "products:read", "parties:read", "inventory:read", "purchases:read",
                             "payables:read", "parties:write", "products:write", "purchases:write", "purchases:receive"}),
    # Deposito: mueve mercaderia; ve pedidos y ordenes para entregar/recibir,
    # no ve cobranzas, pagos, contabilidad ni el tablero.
    R.WAREHOUSE: frozenset({"settings:read", "products:read", "parties:read", "inventory:read", "sales:read",
                            "purchases:read", "inventory:write", "sales:deliver", "purchases:receive"}),
}

ROLE_LABELS: dict[R, str] = {
    R.CLIENT_ADMIN: "Administrador", R.CLIENT_USER: "Consulta", R.AUDITOR: "Auditor", R.MANAGER: "Gerencia",
    R.FINANCE: "Finanzas", R.ACCOUNTANT: "Contador", R.SALES: "Ventas", R.PURCHASING: "Compras",
    R.WAREHOUSE: "Deposito",
}


def permissions_for(role: R) -> frozenset[str]:
    return PERMISSIONS.get(role, frozenset())
