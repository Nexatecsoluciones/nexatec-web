"""Tenant DESCARTABLE para la prueba E2E de la interfaz del ERP (ver docs/E2E.md).

    python scripts/e2e_tenant.py create <archivo.json>   # demo con empresa ficticia + usuario CLIENT_ADMIN
    python scripts/e2e_tenant.py destroy <archivo.json>  # borra base fisica y todas sus filas
    python scripts/e2e_tenant.py create-admin <archivo.json>   # SUPER_ADMIN de prueba (primer ingreso)
    python scripts/e2e_tenant.py destroy-admin <archivo.json>

No pasa por el endpoint de demos, asi que NO crea subdominios ni toca
Cloudflare. `destroy` se niega a borrar cualquier tenant cuyo slug no
empiece con "e2e-". La contrasena (aleatoria) queda solo en el archivo
JSON, creado con permisos 600: borrarlo despues de usarlo."""

import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.db import SessionLocal  # noqa: E402
from app.models import control_center, control_plane, media, payments, system, tenancy  # noqa: E402,F401
from app.models.control_plane import AuditLog, PasswordResetToken, Tenant, User, UserSession  # noqa: E402
from app.models.system import System  # noqa: E402
from app.models.tenancy import SystemAccess, TenantDatabase, TenantDatabaseCredential, TenantUser  # noqa: E402
from app.models.tenancy_enums import (  # noqa: E402
    Environment, SystemAccessStatus, TenantMemberRole, TenantMemberStatus, TenantStatus,
)
from app.security.passwords import hash_password  # noqa: E402
from app.security.roles import Role  # noqa: E402
from app.services.provisioning import force_drop_tenant_database_for_tests, provision_tenant_database  # noqa: E402


def create(path: str) -> None:
    db = SessionLocal()
    sfx = uuid.uuid4().hex[:8]
    system_row = System(slug=f"e2e-erp-{sfx}", name="NEXATEC ERP (prueba E2E)", short_description="x",
                        category="ERP", demo_available=True, production_available=True, is_active=True)
    tenant = Tenant(slug=f"e2e-{sfx}", legal_name="E2E Prueba", display_name="E2E", status=TenantStatus.ACTIVE)
    db.add_all([system_row, tenant])
    db.commit()
    password = secrets.token_urlsafe(18) + "A1"
    user = User(email=f"e2e-{sfx}@example.com", full_name="E2E", password_hash=hash_password(password),
                role=Role.CLIENT_USER, tenant_id=tenant.id)
    db.add(user)
    db.commit()
    db.add(TenantUser(tenant_id=tenant.id, user_id=user.id, role=TenantMemberRole.CLIENT_ADMIN,
                      status=TenantMemberStatus.ACTIVE))
    access = SystemAccess(tenant_id=tenant.id, system_id=system_row.id, environment=Environment.DEMO,
                          status=SystemAccessStatus.ACTIVE, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    db.add(access)
    db.commit()
    provision_tenant_database(db, tenant.id, system_row.id, Environment.DEMO)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"email": user.email, "password": password, "access_id": str(access.id),
                   "tenant_id": str(tenant.id)}, fh)
    print(f"tenant e2e-{sfx} listo; credenciales en {path}")


def destroy(path: str) -> None:
    cfg = json.load(open(path))
    db = SessionLocal()
    tenant = db.get(Tenant, uuid.UUID(cfg["tenant_id"]))
    if tenant is None:
        print("no existe")
        return
    if not tenant.slug.startswith("e2e-"):
        raise SystemExit(f"Me niego: {tenant.slug} no es un tenant de prueba E2E.")
    tid = tenant.id
    for td in db.query(TenantDatabase).filter(TenantDatabase.tenant_id == tid).all():
        force_drop_tenant_database_for_tests(td.database_identifier)
    system_ids = [a.system_id for a in db.query(SystemAccess).filter(SystemAccess.tenant_id == tid).all()]
    uids = [u.id for u in db.query(User).filter(User.tenant_id == tid).all()]
    # Borrados explicitos en orden de dependencias (hijos primero).
    db.query(TenantDatabaseCredential).filter(TenantDatabaseCredential.tenant_database_id.in_(
        db.query(TenantDatabase.id).filter(TenantDatabase.tenant_id == tid))).delete(synchronize_session=False)
    db.query(TenantDatabase).filter(TenantDatabase.tenant_id == tid).delete(synchronize_session=False)
    db.query(SystemAccess).filter(SystemAccess.tenant_id == tid).delete(synchronize_session=False)
    db.query(UserSession).filter(UserSession.user_id.in_(uids)).delete(synchronize_session=False)
    db.query(TenantUser).filter(TenantUser.tenant_id == tid).delete(synchronize_session=False)
    db.query(AuditLog).filter(AuditLog.tenant_id == tid).delete(synchronize_session=False)
    db.query(User).filter(User.id.in_(uids)).delete(synchronize_session=False)
    db.query(Tenant).filter(Tenant.id == tid).delete(synchronize_session=False)
    db.query(System).filter(System.id.in_(system_ids), System.slug.like("e2e-erp-%")).delete(synchronize_session=False)
    db.commit()
    os.remove(path)
    print("tenant de prueba eliminado")


def create_admin(path: str) -> None:
    """SUPER_ADMIN descartable en estado de PRIMER INGRESO (cambio de
    contrasena obligatorio, sin MFA): el mismo estado en que queda el
    superadmin real creado por la CLI."""
    db = SessionLocal()
    password = secrets.token_urlsafe(18) + "A1"
    user = User(email=f"e2e-admin-{uuid.uuid4().hex[:8]}@example.com", full_name="E2E Admin",
                password_hash=hash_password(password), role=Role.SUPER_ADMIN, must_change_password=True)
    db.add(user)
    db.commit()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"email": user.email, "password": password, "user_id": str(user.id)}, fh)
    print(f"admin de prueba {user.email} listo")


def destroy_admin(path: str) -> None:
    cfg = json.load(open(path))
    db = SessionLocal()
    user = db.get(User, uuid.UUID(cfg["user_id"]))
    if user is not None:
        if not user.email.startswith("e2e-admin-"):
            raise SystemExit(f"Me niego: {user.email} no es un admin de prueba.")
        from app.models.control_plane import SecurityEvent
        db.query(UserSession).filter(UserSession.user_id == user.id).delete(synchronize_session=False)
        db.query(PasswordResetToken).filter(PasswordResetToken.user_id == user.id).delete(synchronize_session=False)
        db.query(SecurityEvent).filter(SecurityEvent.user_id == user.id).delete(synchronize_session=False)
        db.query(AuditLog).filter(AuditLog.actor_user_id == user.id).delete(synchronize_session=False)
        db.delete(user)
        db.commit()
    os.remove(path)
    print("admin de prueba eliminado")


if __name__ == "__main__":
    {"create": create, "destroy": destroy, "create-admin": create_admin,
     "destroy-admin": destroy_admin}[sys.argv[1]](sys.argv[2])
