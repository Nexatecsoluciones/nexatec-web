"""Maestros del ERP: empresa, sucursales, depositos, categorias, productos y
terceros. Todo opera sobre la base del tenant resuelta por
app/security/erp_context.py (nunca sobre nexatec_control). Lectura: cualquier
miembro activo; escritura: CLIENT_ADMIN. Cada escritura queda auditada en el
control plane (sin datos sensibles en metadata)."""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, get_erp_context, require_erp_write
from app.services import ruc as ruc_service
from app.tenant_models.core import (
    Branch,
    Company,
    Currency,
    Party,
    Product,
    ProductCategory,
    ProductType,
    RucVerificationStatus,
    Tax,
    UnitOfMeasure,
    Warehouse,
)

router = APIRouter(prefix="/api/erp/{system_access_id}", tags=["erp"])

MAX_PAGE = 200


def _audit(ctx: ErpContext, action: str, resource: str, metadata: dict | None = None) -> None:
    log_audit(
        ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id,
        action=action, resource=resource, metadata=metadata,
    )
    ctx.control_db.commit()


def _commit_or_conflict(ctx: ErpContext, detail: str) -> None:
    try:
        ctx.db.commit()
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _get_or_404(ctx: ErpContext, model, obj_id):
    obj = ctx.db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No encontrado.")
    return obj


def _apply(obj, updates: dict) -> None:
    for field, value in updates.items():
        setattr(obj, field, value)


class Page(BaseModel):
    total: int
    items: list


# --- Referencia (solo lectura) ----------------------------------------------


class CurrencyOut(BaseModel):
    code: str
    name: str
    decimals: int
    model_config = ConfigDict(from_attributes=True)


class UnitOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    model_config = ConfigDict(from_attributes=True)


class TaxOut(BaseModel):
    code: str
    name: str
    rate: Decimal
    model_config = ConfigDict(from_attributes=True)


@router.get("/currencies", response_model=list[CurrencyOut])
def list_currencies(ctx: ErpContext = Depends(get_erp_context)):
    return ctx.db.execute(select(Currency).where(Currency.is_active).order_by(Currency.code)).scalars().all()


@router.get("/units", response_model=list[UnitOut])
def list_units(ctx: ErpContext = Depends(get_erp_context)):
    return ctx.db.execute(select(UnitOfMeasure).order_by(UnitOfMeasure.code)).scalars().all()


@router.get("/taxes", response_model=list[TaxOut])
def list_taxes(ctx: ErpContext = Depends(get_erp_context)):
    """Solo la tasa vigente hoy de cada codigo."""
    today = func.current_date()
    return ctx.db.execute(
        select(Tax)
        .where(Tax.valid_from <= today, or_(Tax.valid_to.is_(None), Tax.valid_to >= today))
        .order_by(Tax.code)
    ).scalars().all()


# --- Empresa ----------------------------------------------------------------


class CompanyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    trade_name: str | None = Field(default=None, max_length=200)
    ruc: str | None = Field(default=None, max_length=8)
    ruc_dv: str | None = Field(default=None, max_length=1)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=40)
    email: EmailStr | None = None


class CompanyOut(BaseModel):
    legal_name: str
    trade_name: str | None
    ruc: str | None
    ruc_dv: str | None
    ruc_is_fictitious: bool
    address: str | None
    phone: str | None
    email: str | None
    base_currency: str
    timezone: str
    model_config = ConfigDict(from_attributes=True)


def _validate_ruc_pair(ruc: str | None, dv: str | None) -> None:
    if ruc is None and dv is None:
        return
    if not ruc or not dv or not ruc_service.is_valid(ruc, dv):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="RUC o digito verificador invalido.",
        )


@router.get("/company", response_model=CompanyOut)
def get_company(ctx: ErpContext = Depends(get_erp_context)):
    return _get_or_404(ctx, Company, 1)


@router.put("/company", response_model=CompanyOut)
def put_company(payload: CompanyIn, ctx: ErpContext = Depends(require_erp_write)):
    company = ctx.db.get(Company, 1)
    if company is not None and company.ruc_is_fictitious:
        # La empresa de una demo es ficticia a proposito; no se le puede
        # cargar un RUC "real" para que sus documentos parezcan validos.
        if payload.ruc is not None and payload.ruc != company.ruc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                                detail="La empresa de demostracion no admite un RUC real.")
    else:
        _validate_ruc_pair(payload.ruc, payload.ruc_dv)

    data = payload.model_dump()
    if company is None:
        company = Company(id=1, base_currency="PYG", timezone="America/Asuncion", ruc_is_fictitious=False, **data)
        ctx.db.add(company)
    else:
        _apply(company, data)
    _commit_or_conflict(ctx, "No se pudo guardar la empresa.")
    _audit(ctx, "ERP_COMPANY_UPDATED", "company:1", {"fields": list(data.keys())})
    ctx.db.refresh(company)
    return company


# --- Sucursales y depositos -------------------------------------------------


class BranchIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    establishment_code: str | None = Field(default=None, pattern=r"^\d{3}$")


class BranchUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = Field(default=None, max_length=300)
    establishment_code: str | None = Field(default=None, pattern=r"^\d{3}$")
    is_active: bool | None = None


class BranchOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    address: str | None
    establishment_code: str | None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


@router.get("/branches", response_model=list[BranchOut])
def list_branches(ctx: ErpContext = Depends(get_erp_context)):
    return ctx.db.execute(select(Branch).order_by(Branch.code)).scalars().all()


@router.post("/branches", response_model=BranchOut, status_code=status.HTTP_201_CREATED)
def create_branch(payload: BranchIn, ctx: ErpContext = Depends(require_erp_write)):
    branch = Branch(**payload.model_dump())
    ctx.db.add(branch)
    _commit_or_conflict(ctx, "Ya existe una sucursal con ese codigo.")
    _audit(ctx, "ERP_BRANCH_CREATED", f"branch:{branch.id}")
    return branch


@router.patch("/branches/{branch_id}", response_model=BranchOut)
def update_branch(branch_id: uuid.UUID, payload: BranchUpdate, ctx: ErpContext = Depends(require_erp_write)):
    branch = _get_or_404(ctx, Branch, branch_id)
    updates = payload.model_dump(exclude_unset=True)
    _apply(branch, updates)
    _commit_or_conflict(ctx, "No se pudo actualizar la sucursal.")
    _audit(ctx, "ERP_BRANCH_UPDATED", f"branch:{branch.id}", {"fields": list(updates.keys())})
    return branch


class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    branch_id: uuid.UUID


class WarehouseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None


class WarehouseOut(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    branch_id: uuid.UUID
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


@router.get("/warehouses", response_model=list[WarehouseOut])
def list_warehouses(ctx: ErpContext = Depends(get_erp_context)):
    return ctx.db.execute(select(Warehouse).order_by(Warehouse.code)).scalars().all()


@router.post("/warehouses", response_model=WarehouseOut, status_code=status.HTTP_201_CREATED)
def create_warehouse(payload: WarehouseIn, ctx: ErpContext = Depends(require_erp_write)):
    _get_or_404(ctx, Branch, payload.branch_id)
    warehouse = Warehouse(**payload.model_dump())
    ctx.db.add(warehouse)
    _commit_or_conflict(ctx, "Ya existe un deposito con ese codigo.")
    _audit(ctx, "ERP_WAREHOUSE_CREATED", f"warehouse:{warehouse.id}")
    return warehouse


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseOut)
def update_warehouse(warehouse_id: uuid.UUID, payload: WarehouseUpdate, ctx: ErpContext = Depends(require_erp_write)):
    warehouse = _get_or_404(ctx, Warehouse, warehouse_id)
    updates = payload.model_dump(exclude_unset=True)
    _apply(warehouse, updates)
    _commit_or_conflict(ctx, "No se pudo actualizar el deposito.")
    _audit(ctx, "ERP_WAREHOUSE_UPDATED", f"warehouse:{warehouse.id}", {"fields": list(updates.keys())})
    return warehouse


# --- Categorias y productos -------------------------------------------------


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    model_config = ConfigDict(from_attributes=True)


@router.get("/product-categories", response_model=list[CategoryOut])
def list_categories(ctx: ErpContext = Depends(get_erp_context)):
    return ctx.db.execute(select(ProductCategory).order_by(ProductCategory.name)).scalars().all()


@router.post("/product-categories", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(payload: CategoryIn, ctx: ErpContext = Depends(require_erp_write)):
    if payload.parent_id is not None:
        _get_or_404(ctx, ProductCategory, payload.parent_id)
    category = ProductCategory(**payload.model_dump())
    ctx.db.add(category)
    _commit_or_conflict(ctx, "Ya existe una categoria con ese nombre.")
    _audit(ctx, "ERP_CATEGORY_CREATED", f"product_category:{category.id}")
    return category


class ProductIn(BaseModel):
    sku: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    product_type: ProductType
    category_id: uuid.UUID | None = None
    unit_id: uuid.UUID
    tax_code: str = Field(max_length=20)
    sale_price: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    tracks_stock: bool = True

    @model_validator(mode="after")
    def services_never_track_stock(self) -> "ProductIn":
        # Si no se mando, un servicio queda sin stock; si se mando True
        # explicito, es un error del cliente, no se corrige en silencio.
        if self.product_type == ProductType.SERVICE:
            if "tracks_stock" in self.model_fields_set and self.tracks_stock:
                raise ValueError("Un servicio no maneja stock.")
            self.tracks_stock = False
        return self


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    category_id: uuid.UUID | None = None
    tax_code: str | None = Field(default=None, max_length=20)
    sale_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    is_active: bool | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    sku: str
    name: str
    description: str | None
    product_type: ProductType
    category_id: uuid.UUID | None
    unit_id: uuid.UUID
    tax_code: str
    sale_price: Decimal
    tracks_stock: bool
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class ProductPage(BaseModel):
    total: int
    items: list[ProductOut]


def _assert_tax_code_exists(ctx: ErpContext, code: str) -> None:
    if ctx.db.execute(select(Tax.id).where(Tax.code == code).limit(1)).first() is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Codigo de impuesto inexistente.")


@router.get("/products", response_model=ProductPage)
def list_products(
    ctx: ErpContext = Depends(get_erp_context),
    q: str | None = Query(default=None, max_length=100),
    active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Product)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Product.name.ilike(like), Product.sku.ilike(like)))
    if active is not None:
        stmt = stmt.where(Product.is_active == active)
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(Product.name).limit(limit).offset(offset)).scalars().all()
    return ProductPage(total=total, items=items)


@router.get("/products/{product_id}", response_model=ProductOut)
def get_product(product_id: uuid.UUID, ctx: ErpContext = Depends(get_erp_context)):
    return _get_or_404(ctx, Product, product_id)


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductIn, ctx: ErpContext = Depends(require_erp_write)):
    _get_or_404(ctx, UnitOfMeasure, payload.unit_id)
    if payload.category_id is not None:
        _get_or_404(ctx, ProductCategory, payload.category_id)
    _assert_tax_code_exists(ctx, payload.tax_code)
    product = Product(**payload.model_dump(), average_cost=Decimal("0"))
    ctx.db.add(product)
    _commit_or_conflict(ctx, "Ya existe un producto con ese SKU.")
    _audit(ctx, "ERP_PRODUCT_CREATED", f"product:{product.id}")
    return product


@router.patch("/products/{product_id}", response_model=ProductOut)
def update_product(product_id: uuid.UUID, payload: ProductUpdate, ctx: ErpContext = Depends(require_erp_write)):
    product = _get_or_404(ctx, Product, product_id)
    updates = payload.model_dump(exclude_unset=True)
    if "tax_code" in updates:
        _assert_tax_code_exists(ctx, updates["tax_code"])
    if updates.get("category_id") is not None:
        _get_or_404(ctx, ProductCategory, updates["category_id"])
    _apply(product, updates)
    _commit_or_conflict(ctx, "No se pudo actualizar el producto.")
    meta = {"fields": list(updates.keys())}
    if "sale_price" in updates:
        meta["sale_price"] = str(updates["sale_price"])
    _audit(ctx, "ERP_PRODUCT_UPDATED", f"product:{product.id}", meta)
    return product


# --- Terceros (clientes / proveedores) --------------------------------------


class PartyIn(BaseModel):
    legal_name: str = Field(min_length=2, max_length=200)
    trade_name: str | None = Field(default=None, max_length=200)
    ruc: str | None = Field(default=None, max_length=8)
    ruc_dv: str | None = Field(default=None, max_length=1)
    is_customer: bool = False
    is_supplier: bool = False
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    payment_terms_days: int = Field(default=0, ge=0, le=365)
    credit_limit: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)


class PartyUpdate(BaseModel):
    legal_name: str | None = Field(default=None, min_length=2, max_length=200)
    trade_name: str | None = Field(default=None, max_length=200)
    is_customer: bool | None = None
    is_supplier: bool | None = None
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=300)
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    credit_limit: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    is_active: bool | None = None


class PartyOut(BaseModel):
    id: uuid.UUID
    legal_name: str
    trade_name: str | None
    ruc: str | None
    ruc_dv: str | None
    ruc_status: RucVerificationStatus
    is_customer: bool
    is_supplier: bool
    email: str | None
    phone: str | None
    address: str | None
    payment_terms_days: int
    credit_limit: Decimal
    is_active: bool
    model_config = ConfigDict(from_attributes=True)


class PartyPage(BaseModel):
    total: int
    items: list[PartyOut]


@router.get("/parties", response_model=PartyPage)
def list_parties(
    ctx: ErpContext = Depends(get_erp_context),
    q: str | None = Query(default=None, max_length=100),
    role: str | None = Query(default=None, pattern="^(customer|supplier)$"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Party)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Party.legal_name.ilike(like), Party.trade_name.ilike(like), Party.ruc.ilike(like)))
    if role == "customer":
        stmt = stmt.where(Party.is_customer.is_(True))
    elif role == "supplier":
        stmt = stmt.where(Party.is_supplier.is_(True))
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    items = ctx.db.execute(stmt.order_by(Party.legal_name).limit(limit).offset(offset)).scalars().all()
    return PartyPage(total=total, items=items)


@router.get("/parties/{party_id}", response_model=PartyOut)
def get_party(party_id: uuid.UUID, ctx: ErpContext = Depends(get_erp_context)):
    return _get_or_404(ctx, Party, party_id)


@router.post("/parties", response_model=PartyOut, status_code=status.HTTP_201_CREATED)
def create_party(payload: PartyIn, ctx: ErpContext = Depends(require_erp_write)):
    if not (payload.is_customer or payload.is_supplier):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Un tercero tiene que ser cliente, proveedor o ambos.")
    _validate_ruc_pair(payload.ruc, payload.ruc_dv)
    # Nunca VERIFIED_PROVIDER desde aca: un DV correcto solo prueba el formato.
    ruc_status = RucVerificationStatus.FORMAT_OK if payload.ruc else RucVerificationStatus.UNVERIFIED
    party = Party(**payload.model_dump(), ruc_status=ruc_status)
    ctx.db.add(party)
    _commit_or_conflict(ctx, "Ya existe un tercero con ese RUC.")
    _audit(ctx, "ERP_PARTY_CREATED", f"party:{party.id}")
    return party


@router.patch("/parties/{party_id}", response_model=PartyOut)
def update_party(party_id: uuid.UUID, payload: PartyUpdate, ctx: ErpContext = Depends(require_erp_write)):
    party = _get_or_404(ctx, Party, party_id)
    updates = payload.model_dump(exclude_unset=True)
    _apply(party, updates)
    if not (party.is_customer or party.is_supplier):
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Un tercero tiene que ser cliente, proveedor o ambos.")
    _commit_or_conflict(ctx, "No se pudo actualizar el tercero.")
    _audit(ctx, "ERP_PARTY_UPDATED", f"party:{party.id}", {"fields": list(updates.keys())})
    return party
