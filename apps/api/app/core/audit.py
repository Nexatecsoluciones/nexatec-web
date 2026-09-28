import uuid

from sqlalchemy.orm import Session

from app.models.control_plane import AuditLog


def log_audit(
    db: Session,
    *,
    actor_user_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    action: str,
    resource: str | None = None,
    ip_address: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Auditoria append-only. No existe update/delete sobre AuditLog en
    ningun router -- ni siquiera para un CLIENT_ADMIN sobre sus propios
    eventos. `metadata` nunca debe incluir secrets/passwords/tokens."""
    db.add(
        AuditLog(
            actor_user_id=actor_user_id,
            tenant_id=tenant_id,
            action=action,
            resource=resource,
            ip_address=ip_address,
            metadata_safe=metadata,
        )
    )
