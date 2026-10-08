"""CRM: prospectos (leads), oportunidades con etapas y actividades.

Un prospecto es alguien que todavia NO es cliente: no se mezcla con el
maestro de terceros hasta que se convierte (convert_lead crea el Party y
deja el vinculo). Una oportunidad pertenece a un tercero o a un prospecto
(nunca a ninguno). Las oportunidades ganadas/perdidas quedan cerradas: no se
editan ni se reabren por la API (la historia del embudo tiene que ser
confiable para el pronostico)."""

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.tenant_models import TenantBase


class LeadStatus(str, enum.Enum):
    OPEN = "OPEN"
    CONVERTED = "CONVERTED"
    DISCARDED = "DISCARDED"


class OpportunityStage(str, enum.Enum):
    NEW = "NEW"
    QUALIFIED = "QUALIFIED"
    PROPOSAL = "PROPOSAL"
    NEGOTIATION = "NEGOTIATION"
    WON = "WON"
    LOST = "LOST"


OPEN_STAGES = (OpportunityStage.NEW, OpportunityStage.QUALIFIED, OpportunityStage.PROPOSAL, OpportunityStage.NEGOTIATION)
# Probabilidad sugerida al entrar a cada etapa (el usuario la puede ajustar).
DEFAULT_PROBABILITY = {
    OpportunityStage.NEW: 10, OpportunityStage.QUALIFIED: 25, OpportunityStage.PROPOSAL: 50,
    OpportunityStage.NEGOTIATION: 75, OpportunityStage.WON: 100, OpportunityStage.LOST: 0,
}


class ActivityKind(str, enum.Enum):
    CALL = "CALL"
    MEETING = "MEETING"
    EMAIL = "EMAIL"
    WHATSAPP = "WHATSAPP"
    TASK = "TASK"
    NOTE = "NOTE"


class Lead(TenantBase):
    __tablename__ = "crm_leads"
    __table_args__ = (
        CheckConstraint("status <> 'CONVERTED' OR party_id IS NOT NULL", name="ck_crm_leads_converted_has_party"),
        CheckConstraint("email IS NOT NULL OR phone IS NOT NULL", name="ck_crm_leads_has_contact"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    contact_name: Mapped[str] = mapped_column(String(200), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus, name="crm_lead_status"), nullable=False, index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    discard_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Opportunity(TenantBase):
    __tablename__ = "crm_opportunities"
    __table_args__ = (
        CheckConstraint("(party_id IS NULL) <> (lead_id IS NULL)", name="ck_crm_opportunities_one_owner"),
        CheckConstraint("amount >= 0", name="ck_crm_opportunities_amount_non_negative"),
        CheckConstraint("probability BETWEEN 0 AND 100", name="ck_crm_opportunities_probability_range"),
        CheckConstraint("stage <> 'LOST' OR lost_reason IS NOT NULL", name="ck_crm_opportunities_lost_has_reason"),
        CheckConstraint("(stage IN ('WON', 'LOST')) = (closed_at IS NOT NULL)", name="ck_crm_opportunities_closed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_leads.id"), index=True)
    stage: Mapped[OpportunityStage] = mapped_column(Enum(OpportunityStage, name="crm_opportunity_stage"), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), ForeignKey("currencies.code"), nullable=False)
    probability: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class Activity(TenantBase):
    __tablename__ = "crm_activities"
    __table_args__ = (
        CheckConstraint("num_nonnulls(party_id, lead_id, opportunity_id) >= 1", name="ck_crm_activities_has_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[ActivityKind] = mapped_column(Enum(ActivityKind, name="crm_activity_kind"), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    party_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("parties.id"), index=True)
    lead_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_leads.id"), index=True)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("crm_opportunities.id"), index=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
