"""Matriz de permisos del ERP: (1) toda ruta /api/erp/... tiene un permiso
explicito salvo una lista blanca minima, (2) la matriz aplicada en
endpoints reales con bases reales, rol por rol."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.core.db import SessionLocal
from app.main import app
from app.models.control_plane import AuditLog, Tenant, User, UserSession
from app.models.system import System
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser
from app.models.tenancy_enums import Environment, SystemAccessStatus, TenantMemberRole as R, TenantMemberStatus, TenantStatus
from app.security.erp_permissions import PERMISSIONS
from app.security.passwords import hash_password
from app.security.roles import Role
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database

PASSWORD = "ClaveDePruebaSegura123"
UNGUARDED_OK = {"/context", "/currencies", "/units", "/taxes"}


def test_every_erp_route_requires_an_explicit_permission():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/erp/"):
            continue
        suffix = route.path.split("{system_access_id}", 1)[1]
        guarded = any(getattr(d.call, "__qualname__", "").startswith("require.<locals>")
                      for d in route.dependant.dependencies)
        if not guarded and suffix not in UNGUARDED_OK:
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, f"Rutas del ERP sin permiso explicito: {missing}"


def test_every_role_is_in_the_matrix():
    assert set(R) == set(PERMISSIONS)


@pytest.fixture(scope="module")
def roles():
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system = System(slug=f"pytest-perm-{sfx}", name="Perm", short_description="x", category="Test",
                    demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"perm-{sfx}", legal_name="Perm", display_name="Perm", status=TenantStatus.ACTIVE)
    db.add_all([system, tenant])
    db.commit()
    access = SystemAccess(tenant_id=tenant.id, system_id=system.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=7))
    db.add(access)
    db.commit()
    tdb = provision_tenant_database(db, tenant.id, system.id, Environment.DEMO)
    users, clients = [], {}
    for role in R:
        u = User(email=f"perm-{role.value.lower()}-{sfx}@example.com", password_hash=hash_password(PASSWORD),
                 role=Role.CLIENT_USER, tenant_id=tenant.id)
        db.add(u)
        db.commit()
        db.add(TenantUser(tenant_id=tenant.id, user_id=u.id, role=role, status=TenantMemberStatus.ACTIVE))
        db.commit()
        users.append(u)
        c = TestClient(app)
        assert c.post("/api/auth/login", json={"email": u.email, "password": PASSWORD, "turnstile_token": "dev"}).status_code == 200
        clients[role] = c
    yield {"c": clients, "base": f"/api/erp/{access.id}"}
    force_drop_tenant_database_for_tests(tdb.database_identifier)
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id == tdb.id).delete()
    db.query(TenantDatabase).filter(TenantDatabase.id == tdb.id).delete()
    uids = [u.id for u in users]
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tenant.id).delete()
    db.query(SystemAccess).filter(SystemAccess.id == access.id).delete()
    db.query(AuditLog).filter(AuditLog.tenant_id == tenant.id).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tenant.id).delete()
    db.query(System).filter(System.id == system.id).delete()
    db.commit()
    db.close()


READS = {
    "accounting": "/accounting/reports/trial-balance", "payables": "/payables/aging", "receivables": "/receivables/aging",
    "dashboard": "/dashboard", "sales": "/sales/orders", "inventory": "/stock/balances", "products": "/products",
}


@pytest.mark.parametrize("role", list(R))
def test_reads_follow_the_matrix(roles, role):
    c = roles["c"][role]
    for module, path in READS.items():
        expected = 200 if f"{module}:read" in PERMISSIONS[role] else 403
        assert c.get(f"{roles['base']}{path}").status_code == expected, (role, module)
    ctx = c.get(f"{roles['base']}/context").json()
    assert set(ctx["permissions"]) == set(PERMISSIONS[role]) and ctx["role_label"]


def test_writes_follow_the_matrix(roles):
    base, c = roles["base"], roles["c"]
    parties = c[R.CLIENT_ADMIN].get(f"{base}/parties", params={"role": "customer", "limit": 1}).json()["items"]
    products = c[R.CLIENT_ADMIN].get(f"{base}/products", params={"q": "Agua"}).json()["items"]
    whs = c[R.CLIENT_ADMIN].get(f"{base}/warehouses").json()
    order = {"customer_id": parties[0]["id"], "warehouse_id": whs[0]["id"], "payment_condition": "CASH",
             "lines": [{"product_id": products[0]["id"], "quantity": "1"}]}
    # Ventas puede vender; Deposito y Finanzas no pueden crear pedidos.
    assert c[R.SALES].post(f"{base}/sales/orders", json=order).status_code == 201
    assert c[R.WAREHOUSE].post(f"{base}/sales/orders", json=order).status_code == 403
    assert c[R.FINANCE].post(f"{base}/sales/orders", json=order).status_code == 403
    # Deposito mueve stock; Ventas no.
    move = {"product_id": products[0]["id"], "warehouse_id": whs[0]["id"], "quantity": "1", "unit_cost": "100"}
    assert c[R.WAREHOUSE].post(f"{base}/stock/receipts", json=move).status_code == 201
    assert c[R.SALES].post(f"{base}/stock/receipts", json=move).status_code == 403
    # Solo Finanzas/Contador/Admin cierran periodos; Gerencia no.
    today = datetime.now(timezone.utc)
    assert c[R.MANAGER].post(f"{base}/accounting/periods/{today.year}/1/close", json={"note": "intento"}).status_code == 403
    assert c[R.ACCOUNTANT].post(f"{base}/accounting/periods/2000/1/close", json={"note": "cierre viejo"}).status_code == 200
    # Consulta y Auditor no escriben nada.
    for ro in (R.CLIENT_USER, R.AUDITOR):
        assert c[ro].post(f"{base}/sales/orders", json=order).status_code == 403
        assert c[ro].post(f"{base}/parties", json={"legal_name": "X", "is_customer": True}).status_code == 403
