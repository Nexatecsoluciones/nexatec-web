import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import log_audit
from app.core.crypto import encrypt_secret
from app.core.db import get_db
from app.models.control_center import InstanceConfiguration, InstanceSecretValue
from app.models.system import System
from app.models.tenancy_enums import Environment
from app.security.rbac import require_admin_panel
from app.services.config_schema import (
    SchemaValidationError,
    validate_config_against_schema,
    validate_modules_against_schema,
)

router = APIRouter(prefix="/api/admin/config", tags=["admin", "config"])


class ConfigVersionOut(BaseModel):
    id: uuid.UUID
    version: int
    config: dict
    modules: dict
    branding: dict
    created_by_user_id: uuid.UUID | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SaveConfigRequest(BaseModel):
    config: dict = {}
    modules: dict = {}
    branding: dict = {}


class SecretStatusOut(BaseModel):
    key: str
    configured: bool
    updated_at: datetime | None


class SetSecretRequest(BaseModel):
    value: str


def _get_current(db: Session, tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment) -> InstanceConfiguration | None:
    return db.execute(
        select(InstanceConfiguration)
        .where(
            InstanceConfiguration.tenant_id == tenant_id,
            InstanceConfiguration.system_id == system_id,
            InstanceConfiguration.environment == environment,
        )
        .order_by(InstanceConfiguration.version.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("/{tenant_id}/{system_id}/{environment}", response_model=ConfigVersionOut)
def get_current_config(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    current = _get_current(db, tenant_id, system_id, environment)
    if current is None:
        # Todavia no se guardo ninguna version: version 0 vacia, no un 404
        # -- el Configuration Center siempre puede mostrar el formulario.
        return ConfigVersionOut(
            id=uuid.uuid4(), version=0, config={}, modules={}, branding={},
            created_by_user_id=None, created_at=datetime.now(),
        )
    return current


@router.get("/{tenant_id}/{system_id}/{environment}/history", response_model=list[ConfigVersionOut])
def get_config_history(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    return db.execute(
        select(InstanceConfiguration)
        .where(
            InstanceConfiguration.tenant_id == tenant_id,
            InstanceConfiguration.system_id == system_id,
            InstanceConfiguration.environment == environment,
        )
        .order_by(InstanceConfiguration.version.desc())
        .limit(50)
    ).scalars().all()


@router.post("/{tenant_id}/{system_id}/{environment}", response_model=ConfigVersionOut, status_code=status.HTTP_201_CREATED)
def save_config(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment, payload: SaveConfigRequest,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    """Cada guardado crea una VERSION NUEVA (append-only, ver
    app/models/control_center.py). "Volver a una version anterior" se hace
    llamando este mismo endpoint con el config/modules/branding de esa
    version vieja -- nunca se reescribe una fila existente."""
    system = db.get(System, system_id)
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sistema no encontrado.")

    try:
        clean_config = validate_config_against_schema(system.config_schema or [], payload.config)
        clean_modules = validate_modules_against_schema(system.modules_schema or [], payload.modules)
    except SchemaValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    current = _get_current(db, tenant_id, system_id, environment)
    next_version = (current.version + 1) if current else 1

    new_version = InstanceConfiguration(
        tenant_id=tenant_id, system_id=system_id, environment=environment, version=next_version,
        config=clean_config, modules=clean_modules, branding=payload.branding or {},
        created_by_user_id=admin.id,
    )
    db.add(new_version)
    log_audit(db, actor_user_id=admin.id, tenant_id=tenant_id, action="CONFIGURATION_SAVED",
               resource=f"instance_configuration:{system_id}:{environment.value}", metadata={"version": next_version})
    db.commit()
    db.refresh(new_version)
    return new_version


@router.get("/{tenant_id}/{system_id}/{environment}/secrets", response_model=list[SecretStatusOut])
def list_secret_status(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    """Nunca devuelve el valor -- solo si esta configurado o no. Ni
    siquiera SUPER_ADMIN puede leer un secret ya guardado desde aca."""
    rows = db.execute(
        select(InstanceSecretValue).where(
            InstanceSecretValue.tenant_id == tenant_id,
            InstanceSecretValue.system_id == system_id,
            InstanceSecretValue.environment == environment,
        )
    ).scalars().all()
    return [SecretStatusOut(key=r.key, configured=True, updated_at=r.updated_at) for r in rows]


@router.put("/{tenant_id}/{system_id}/{environment}/secrets/{key}", status_code=status.HTTP_204_NO_CONTENT)
def set_secret(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment, key: str, payload: SetSecretRequest,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    system = db.get(System, system_id)
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sistema no encontrado.")
    allowed_keys = {f["key"] for f in (system.config_schema or []) if f.get("type") == "secret"}
    if key not in allowed_keys:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Esa key no es un campo secret definido en el producto.")

    existing = db.execute(
        select(InstanceSecretValue).where(
            InstanceSecretValue.tenant_id == tenant_id, InstanceSecretValue.system_id == system_id,
            InstanceSecretValue.environment == environment, InstanceSecretValue.key == key,
        )
    ).scalar_one_or_none()

    encrypted = encrypt_secret(payload.value)
    if existing:
        existing.encrypted_value = encrypted
        existing.updated_by_user_id = admin.id
    else:
        db.add(InstanceSecretValue(
            tenant_id=tenant_id, system_id=system_id, environment=environment, key=key,
            encrypted_value=encrypted, updated_by_user_id=admin.id,
        ))

    log_audit(db, actor_user_id=admin.id, tenant_id=tenant_id, action="SECRET_CONFIG_SET",
               resource=f"instance_secret:{system_id}:{environment.value}:{key}")
    db.commit()


@router.delete("/{tenant_id}/{system_id}/{environment}/secrets/{key}", status_code=status.HTTP_204_NO_CONTENT)
def delete_secret(
    tenant_id: uuid.UUID, system_id: uuid.UUID, environment: Environment, key: str,
    db: Session = Depends(get_db), admin=Depends(require_admin_panel()),
):
    db.query(InstanceSecretValue).filter(
        InstanceSecretValue.tenant_id == tenant_id, InstanceSecretValue.system_id == system_id,
        InstanceSecretValue.environment == environment, InstanceSecretValue.key == key,
    ).delete()
    log_audit(db, actor_user_id=admin.id, tenant_id=tenant_id, action="SECRET_CONFIG_DELETED",
               resource=f"instance_secret:{system_id}:{environment.value}:{key}")
    db.commit()
