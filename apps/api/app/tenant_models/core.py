"""Maestros del nucleo del ERP. Dinero siempre NUMERIC (nunca float),
fechas con zona horaria (UTC en DB, se muestran en America/Asuncion).

Cada base de tenant representa UNA empresa (database-per-tenant), por eso
`Company` es una tabla de una sola fila y el resto no lleva company_id."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.tenant_models import TenantBase


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ProductType(str, enum.Enum):
    GOOD = "GOOD"
    SERVICE = "SERVICE"


class RucVerificationStatus(str, enum.Enum):
    """De donde sale la validez del RUC de un tercero. Nunca se asume que
    un RUC esta verificado solo porque el formato/DV es correcto."""

    UNVERIFIED = "UNVERIFIED"
    FORMAT_OK = "FORMAT_OK"
    VERIFIED_PROVIDER = "VERIFIED_PROVIDER"
    FICTITIOUS = "FICTITIOUS"


class Company(TenantBase):
    __tablename__ = "company"
    # Una sola fila por base: el CHECK sobre una PK fija lo garantiza a
    # nivel de base de datos, no solo de aplicacion.
    __table_args__ = (CheckConstraint("id = 1", name="ck_company_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    ruc: Mapped[str | None] = mapped_column(String(20))
    ruc_dv: Mapped[str | None] = mapped_column(String(1))
    # True en demos: el RUC es inventado y cualquier documento que lo use
    # tiene que llevar marca de simulacion.
    ruc_is_fictitious: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    address: Mapped[str | None] = mapped_column(String(300))
    phone: Mapped[str | None] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(255))
    base_currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False, default="PYG")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Asuncion")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Currency(TenantBase):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(String(3), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    decimals: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Branch(TenantBase):
    __tablename__ = "branches"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str | None] = mapped_column(String(300))
    # Codigo de establecimiento tributario (3 digitos, ej. "001"). Se
    # guarda pero NO se usa para emitir nada hasta que SIFEN este habilitado.
    establishment_code: Mapped[str | None] = mapped_column(String(3))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Warehouse(TenantBase):
    __tablename__ = "warehouses"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    branch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("branches.id"), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class UnitOfMeasure(TenantBase):
    __tablename__ = "units_of_measure"

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)


class Tax(TenantBase):
    """Tasa versionada por vigencia: un cambio legal crea una fila nueva
    con otro valid_from, nunca se edita la tasa de una fila existente
    (los documentos viejos siguen apuntando a la tasa con la que se
    hicieron)."""

    __tablename__ = "taxes"
    __table_args__ = (
        UniqueConstraint("code", "valid_from", name="uq_taxes_code_valid_from"),
        CheckConstraint("rate >= 0 AND rate <= 100", name="ck_taxes_rate_range"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[date | None] = mapped_column(Date)


class ProductCategory(TenantBase):
    __tablename__ = "product_categories"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_categories.id"))


class Product(TenantBase):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("sale_price >= 0", name="ck_products_sale_price_non_negative"),
        CheckConstraint("average_cost >= 0", name="ck_products_average_cost_non_negative"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    sku: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[ProductType] = mapped_column(Enum(ProductType, name="product_type"), nullable=False)
    category_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("product_categories.id"), index=True)
    unit_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("units_of_measure.id"), nullable=False)
    tax_code: Mapped[str] = mapped_column(String(20), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    average_cost: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    tracks_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()


class Party(TenantBase):
    """Cliente y/o proveedor (un mismo tercero puede ser ambos sin
    duplicar el maestro)."""

    __tablename__ = "parties"
    __table_args__ = (
        CheckConstraint("is_customer OR is_supplier", name="ck_parties_has_role"),
        CheckConstraint("credit_limit >= 0", name="ck_parties_credit_limit_non_negative"),
        CheckConstraint("payment_terms_days >= 0", name="ck_parties_payment_terms_non_negative"),
        UniqueConstraint("ruc", name="uq_parties_ruc"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    trade_name: Mapped[str | None] = mapped_column(String(200))
    ruc: Mapped[str | None] = mapped_column(String(20))
    ruc_dv: Mapped[str | None] = mapped_column(String(1))
    ruc_status: Mapped[RucVerificationStatus] = mapped_column(
        Enum(RucVerificationStatus, name="ruc_verification_status"),
        nullable=False, default=RucVerificationStatus.UNVERIFIED,
    )
    is_customer: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_supplier: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    address: Mapped[str | None] = mapped_column(String(300))
    payment_terms_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False, default=Decimal("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()
