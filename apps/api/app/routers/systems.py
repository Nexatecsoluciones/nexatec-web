from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.system import System
from app.security.rbac import require_admin_panel

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
    sort_order: int

    model_config = ConfigDict(from_attributes=True)


# Schema explicito de entrada: el cliente NUNCA puede setear campos que no
# aparecen aqui (protege contra mass assignment, p.ej. is_active arbitrario
# desde un endpoint que no sea admin).
class SystemCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=2, max_length=120)
    short_description: str = Field(min_length=2, max_length=240)
    description: str | None = None
    category: str = Field(min_length=2, max_length=80)
    demo_available: bool = False
    production_available: bool = True
    is_active: bool = True
    sort_order: int = 0


@router.get("", response_model=list[SystemOut])
def list_public_systems(db: Session = Depends(get_db)):
    systems = db.execute(
        select(System).where(System.is_active.is_(True)).order_by(System.sort_order)
    ).scalars().all()
    return systems


@admin_router.get("", response_model=list[SystemOut], dependencies=[Depends(require_admin_panel())])
def list_all_systems(db: Session = Depends(get_db)):
    systems = db.execute(select(System).order_by(System.sort_order)).scalars().all()
    return systems


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
