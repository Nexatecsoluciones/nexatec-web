"""CLI administrativo. Unico mecanismo para crear el primer SUPER_ADMIN --
a proposito no existe registro publico ni endpoint HTTP para esto (ver
app/routers/system_status.py::bootstrap_status). Requiere acceso a esta
maquina/terminal, no a la red.

Uso:
    cd apps/api && source .venv/bin/activate
    python -m app.cli create-superadmin
    python -m app.cli list-superadmins
    python -m app.cli promote-user <email>
    python -m app.cli disable-user <email>
    python -m app.cli reset-mfa <email>
    python -m app.cli sweep-expired-demos
    python -m app.cli migrate-tenants
"""

import argparse
import getpass
import sys
import uuid

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from app.core.audit import log_audit
from app.core.db import SessionLocal
from app.models.control_plane import User
from app.security.passwords import WeakPasswordError, hash_password
from app.routers.demos import sweep_expired_demos
from app.security.roles import ADMIN_ROLES, Role
from app.security.session_auth import revoke_all_sessions_for_user

_email_adapter = TypeAdapter(EmailStr)


def _prompt_email(label: str = "Email") -> str:
    while True:
        raw = input(f"{label}: ").strip()
        try:
            return str(_email_adapter.validate_python(raw)).lower()
        except ValidationError:
            print("  Email invalido, intenta de nuevo.")


def _prompt_password() -> str:
    while True:
        password = getpass.getpass("Contrasena: ")
        confirm = getpass.getpass("Confirmar contrasena: ")
        if password != confirm:
            print("  Las contrasenas no coinciden, intenta de nuevo.")
            continue
        return password


def cmd_create_superadmin(_args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        existing_admin = db.execute(
            select(User).where(User.role == Role.SUPER_ADMIN, User.deleted_at.is_(None))
        ).scalar_one_or_none()
        if existing_admin is not None:
            print("Ya existe SUPER_ADMIN.")
            print(f"  Email: {existing_admin.email}")
            print("Usa 'list-superadmins', 'promote-user' o 'disable-user' si necesitas otra accion.")
            return 1

        print("=== Crear SUPER_ADMIN ===")
        email = _prompt_email()

        if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
            print(f"Ya existe un usuario con el email {email}.")
            return 1

        full_name = input("Nombre: ").strip()
        if not full_name:
            print("El nombre no puede estar vacio.")
            return 1

        password = _prompt_password()
        try:
            password_hash = hash_password(password)
        except WeakPasswordError as exc:
            print(f"  Contrasena invalida: {exc}")
            return 1
        finally:
            password = "x" * len(password)  # no deja el valor original en memoria mas de lo necesario
        del password

        user = User(
            id=uuid.uuid4(),
            email=email,
            full_name=full_name,
            password_hash=password_hash,
            role=Role.SUPER_ADMIN,
            is_active=True,
            # Primer acceso: cambio obligatorio + configurar MFA (exigido en
            # el Control Center por NEXATEC_REQUIRE_ADMIN_MFA).
            must_change_password=True,
        )
        db.add(user)
        db.flush()
        log_audit(
            db, actor_user_id=user.id, tenant_id=None, action="SUPER_ADMIN_BOOTSTRAP",
            resource=f"user:{user.id}", metadata={"method": "cli"},
        )
        db.commit()

        print()
        print("SUPER_ADMIN creado correctamente")
        print("En el primer ingreso se pedira cambiar la contrasena y configurar MFA (app autenticadora).")
        print(f"Email: {user.email}")
        print(f"User ID: {user.id}")
        return 0
    finally:
        db.close()


def cmd_list_superadmins(_args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        admins = db.execute(
            select(User).where(User.role.in_(ADMIN_ROLES), User.deleted_at.is_(None)).order_by(User.created_at)
        ).scalars().all()
        if not admins:
            print("No hay usuarios administrativos todavia.")
            return 0
        print(f"{'EMAIL':<40} {'ROL':<14} {'ACTIVO':<8} {'ID'}")
        for a in admins:
            print(f"{a.email:<40} {a.role.value:<14} {'si' if a.is_active else 'no':<8} {a.id}")
        return 0
    finally:
        db.close()


def cmd_promote_user(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if user is None:
            print(f"No existe una cuenta con el email {args.email}.")
            print("promote-user no crea usuarios nuevos -- la persona debe registrarse/tener cuenta antes.")
            return 1

        print(f"Usuario: {user.email} (rol actual: {user.role.value})")
        confirm = input(f"Promover a {args.role}? escribi 'si' para confirmar: ").strip().lower()
        if confirm != "si":
            print("Cancelado.")
            return 1

        old_role = user.role
        user.role = Role(args.role)
        log_audit(
            db, actor_user_id=user.id, tenant_id=None, action="USER_ROLE_PROMOTED",
            resource=f"user:{user.id}", metadata={"from": old_role.value, "to": user.role.value, "method": "cli"},
        )
        db.commit()
        print(f"OK. {user.email} ahora es {user.role.value}.")
        return 0
    finally:
        db.close()


def cmd_disable_user(args: argparse.Namespace) -> int:
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if user is None:
            print(f"No existe una cuenta con el email {args.email}.")
            return 1

        confirm = input(f"Deshabilitar {user.email} y revocar sus sesiones activas? escribi 'si': ").strip().lower()
        if confirm != "si":
            print("Cancelado.")
            return 1

        user.is_active = False
        revoke_all_sessions_for_user(db, user.id)
        log_audit(db, actor_user_id=user.id, tenant_id=user.tenant_id, action="USER_DISABLED",
                   resource=f"user:{user.id}", metadata={"method": "cli"})
        db.commit()
        print(f"OK. {user.email} deshabilitado y sus sesiones revocadas.")
        return 0
    finally:
        db.close()


def cmd_reset_mfa(args: argparse.Namespace) -> int:
    """Recuperacion si se perdio el telefono: solo desde la terminal del
    servidor (la misma raiz de confianza que crea el superadmin)."""
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if user is None:
            print(f"No existe una cuenta con el email {args.email}.")
            return 1
        confirm = input(f"Desactivar MFA de {user.email} y cerrar todas sus sesiones? escribi 'si': ").strip().lower()
        if confirm != "si":
            print("Cancelado.")
            return 1
        user.mfa_enabled = False
        user.mfa_totp_secret = None
        user.mfa_last_step = None
        revoke_all_sessions_for_user(db, user.id)
        log_audit(db, actor_user_id=user.id, tenant_id=user.tenant_id, action="MFA_RESET",
                  resource=f"user:{user.id}", metadata={"method": "cli"})
        db.commit()
        print(f"OK. MFA desactivado para {user.email}; al ingresar debera configurarlo de nuevo.")
        return 0
    finally:
        db.close()


def cmd_sweep_expired_demos(_args: argparse.Namespace) -> int:
    """Pensado para correr periodicamente via systemd timer (ver
    scripts/sweep-expired-demos.sh y docs/RUNBOOK.md) -- no reemplaza el
    chequeo server-side de expires_at en app/routers/portal.py, que
    bloquea acceso igual aunque este comando nunca corra. Esto es lo que
    hace que la demo vencida tambien desaparezca de /portal/my-systems
    como ACTIVE sin esperar a que alguien intente entrar."""
    db = SessionLocal()
    try:
        count = sweep_expired_demos(db)
        print(f"Demos expiradas: {count}")
        return 0
    finally:
        db.close()


def cmd_migrate_tenants(_args: argparse.Namespace) -> int:
    """Lleva TODAS las bases de tenant READY a la ultima revision de
    tenant_alembic/. Una por una (nunca en paralelo, no compite por
    recursos con hesed-ot en el mismo PostgreSQL), y una falla en una base
    no frena las demas -- se reporta al final con codigo de salida != 0."""
    from app.models.tenancy import TenantDatabase
    from app.models.tenancy_enums import ProvisioningStatus
    from app.services.tenant_migrations import TenantMigrationError, head_revision, migrate_tenant_database

    head = head_revision()
    db = SessionLocal()
    failed = 0
    try:
        rows = db.execute(
            select(TenantDatabase).where(TenantDatabase.status == ProvisioningStatus.READY)
            .order_by(TenantDatabase.created_at)
        ).scalars().all()
        print(f"Head: {head}. Bases READY: {len(rows)}")
        for tenant_db in rows:
            if tenant_db.schema_version == head:
                print(f"  {tenant_db.database_identifier}: ya en head")
                continue
            try:
                rev = migrate_tenant_database(db, tenant_db)
                print(f"  {tenant_db.database_identifier}: {tenant_db.schema_version or '-'} -> {rev}")
            except TenantMigrationError:
                failed += 1
                print(f"  {tenant_db.database_identifier}: FALLO (ver logs)")
        return 1 if failed else 0
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="CLI administrativo de NEXATEC")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("create-superadmin", help="Crea el primer SUPER_ADMIN (falla si ya existe uno)")
    sub.add_parser("list-superadmins", help="Lista usuarios con rol administrativo global")

    p_promote = sub.add_parser("promote-user", help="Promueve un usuario existente a un rol administrativo")
    p_promote.add_argument("email")
    p_promote.add_argument("--role", default="SUPER_ADMIN", choices=[r.value for r in ADMIN_ROLES])

    p_disable = sub.add_parser("disable-user", help="Deshabilita un usuario y revoca sus sesiones")
    p_disable.add_argument("email")

    p_mfa = sub.add_parser("reset-mfa", help="Desactiva el MFA de un usuario (telefono perdido)")
    p_mfa.add_argument("email")

    sub.add_parser("sweep-expired-demos", help="Marca EXPIRED los entitlements de demo vencidos")
    sub.add_parser("migrate-tenants", help="Lleva todas las bases de tenant READY a la ultima migracion")

    args = parser.parse_args()
    handlers = {
        "create-superadmin": cmd_create_superadmin,
        "list-superadmins": cmd_list_superadmins,
        "promote-user": cmd_promote_user,
        "disable-user": cmd_disable_user,
        "reset-mfa": cmd_reset_mfa,
        "sweep-expired-demos": cmd_sweep_expired_demos,
        "migrate-tenants": cmd_migrate_tenants,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
