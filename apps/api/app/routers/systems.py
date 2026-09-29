from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.system import ImplementationStatus, System
from app.security.rbac import require_admin_panel
from app.services.config_schema import SchemaValidationError, validate_schema

router = APIRouter(prefix="/api/systems", tags=["systems"])
admin_router = APIRouter(prefix="/api/admin/systems", tags=["admin", "systems"])


class SystemOut(BaseModel):
    id: UUID
    slug: str
    name: str
    short_description: str
    description: str | None
    category: str
    demo_available: bool
    production_available: bool
    is_active: bool
    is_public: bool
    sort_order: int
    implementation_status: ImplementationStatus
    icon: str | None
    image_url: str | None
    video_url: str | None
    default_demo_duration_days: int
    default_max_users: int | None
    default_storage_mb: int | None
    config_schema: list | None
    modules_schema: list | None

    model_config = ConfigDict(from_attributes=True)


# Schema explicito de entrada: el cliente NUNCA puede setear campos que no
# aparecen aqui (protege contra mass assignment, p.ej. is_active arbitrario
# desde un endpoint que no sea admin). config_schema/modules_schema pasan
# SIEMPRE por validate_schema -- nunca se guarda una forma no reconocida
# (ver app/services/config_schema.py: son datos, jamas codigo ejecutable).
class SystemWriteBase(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    short_description: str = Field(min_length=2, max_length=240)
    description: str | None = None
    category: str = Field(min_length=2, max_length=80)
    demo_available: bool = False
    production_available: bool = True
    is_active: bool = True
    is_public: bool = False
    sort_order: int = 0
    implementation_status: ImplementationStatus = ImplementationStatus.PENDING
    icon: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    default_demo_duration_days: int = Field(default=14, ge=1, le=365)
    default_max_users: int | None = Field(default=None, ge=1)
    default_storage_mb: int | None = Field(default=None, ge=1)
    config_schema: list | None = None
    modules_schema: list | None = None

    @field_validator("config_schema", "modules_schema")
    @classmethod
    def _validate_schema_shape(cls, v):
        if v is None:
            return v
        try:
            return validate_schema(v)
        except SchemaValidationError as exc:
            raise ValueError(str(exc))


class SystemCreate(SystemWriteBase):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")


class SystemUpdate(SystemWriteBase):
    pass


@router.get("", response_model=list[SystemOut])
def list_public_systems(db: Session = Depends(get_db)):
    # Catalogo publico ( / y /soluciones ): SOLO templates marcados
    # is_public=true. is_active por si solo ya no alcanza -- un producto
    # puede estar activo para operacion interna sin publicitarse todavia.
    systems = db.execute(
        select(System)
        .where(System.is_active.is_(True), System.is_public.is_(True))
        .order_by(System.sort_order)
    ).scalars().all()
    return systems


@admin_router.get("", response_model=list[SystemOut], dependencies=[Depends(require_admin_panel())])
def list_all_systems(db: Session = Depends(get_db)):
    systems = db.execute(select(System).order_by(System.sort_order)).scalars().all()
    return systems


@admin_router.get("/{system_id}", response_model=SystemOut, dependencies=[Depends(require_admin_panel())])
def get_system(system_id: UUID, db: Session = Depends(get_db)):
    system = db.get(System, system_id)
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sistema no encontrado.")
    return system


@admin_router.post(
    "", response_model=SystemOut, status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin_panel())],
)
def create_system(payload: SystemCreate, db: Session = Depends(get_db)):
    existing = db.execute(select(System).where(System.slug == payload.slug)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El slug ya existe.")

    system = System(**payload.model_dump())
    db.add(system)
    db.commit()
    db.refresh(system)
    return system


@admin_router.patch("/{system_id}", response_model=SystemOut, dependencies=[Depends(require_admin_panel())])
def update_system(system_id: UUID, payload: SystemUpdate, db: Session = Depends(get_db)):
    system = db.get(System, system_id)
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sistema no encontrado.")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(system, field, value)

    db.commit()
    db.refresh(system)
    return system
