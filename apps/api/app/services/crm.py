"""Reglas del CRM. Errores con SalesError/InvalidTransition para reutilizar
el mismo manejo HTTP que el resto del ERP (422 / 409)."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.services.sales import InvalidTransition, SalesError, _currency, next_number
from app.tenant_models.core import Party
from app.tenant_models.crm import (
    DEFAULT_PROBABILITY, OPEN_STAGES, Activity, ActivityKind, Lead, LeadStatus, Opportunity, OpportunityStage,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _lock(db: Session, model, obj_id: uuid.UUID):
    obj = db.execute(
        select(model).where(model.id == obj_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if obj is None:
        raise SalesError("Registro inexistente.")
    return obj


def _customer(db: Session, party_id: uuid.UUID) -> Party:
    party = db.get(Party, party_id)
    if party is None or not party.is_customer or not party.is_active:
        raise SalesError("El cliente no existe, no es cliente o esta inactivo.")
    return party


def _open_lead(db: Session, lead_id: uuid.UUID) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.status != LeadStatus.OPEN:
        raise SalesError("El prospecto no existe o ya no esta abierto.")
    return lead


# --- Prospectos -----------------------------------------------------------------------


def create_lead(db: Session, *, user_id, contact_name: str, company_name: str | None, email: str | None,
                phone: str | None, source: str | None, owner_user_id, notes: str | None) -> Lead:
    if not email and not phone:
        raise SalesError("El prospecto necesita al menos un email o un telefono.")
    lead = Lead(id=uuid.uuid4(), contact_name=contact_name, company_name=company_name, email=email, phone=phone,
                source=source, status=LeadStatus.OPEN, owner_user_id=owner_user_id or user_id, notes=notes,
                created_by_user_id=user_id)
    db.add(lead)
    db.flush()
    return lead


def update_lead(db: Session, lead_id: uuid.UUID, changes: dict) -> Lead:
    lead = _lock(db, Lead, lead_id)
    if lead.status != LeadStatus.OPEN:
        raise InvalidTransition("Solo se editan prospectos abiertos.")
    for k, v in changes.items():
        setattr(lead, k, v)
    if not lead.email and not lead.phone:
        raise SalesError("El prospecto necesita al menos un email o un telefono.")
    return lead


def discard_lead(db: Session, lead_id: uuid.UUID, reason: str) -> Lead:
    lead = _lock(db, Lead, lead_id)
    if lead.status != LeadStatus.OPEN:
        raise InvalidTransition("Solo se descartan prospectos abiertos.")
    if db.execute(select(func.count()).select_from(Opportunity)
                  .where(Opportunity.lead_id == lead.id, Opportunity.stage.in_(OPEN_STAGES))).scalar_one():
        raise InvalidTransition("El prospecto tiene oportunidades abiertas: cerralas primero.")
    lead.status = LeadStatus.DISCARDED
    lead.discard_reason = reason
    return lead


def convert_lead(db: Session, lead_id: uuid.UUID) -> Lead:
    """Crea el cliente en el maestro de terceros (sin RUC: se completa y
    valida despues en Terceros) y pasa al cliente las oportunidades y
    actividades del prospecto."""
    lead = _lock(db, Lead, lead_id)
    if lead.status != LeadStatus.OPEN:
        raise InvalidTransition("Solo se convierten prospectos abiertos.")
    party = Party(id=uuid.uuid4(), legal_name=lead.company_name or lead.contact_name, is_customer=True,
                  is_supplier=False, email=lead.email, phone=lead.phone)
    db.add(party)
    db.flush()
    lead.status = LeadStatus.CONVERTED
    lead.party_id = party.id
    for opp in db.execute(select(Opportunity).where(Opportunity.lead_id == lead.id).with_for_update()).scalars():
        opp.lead_id = None
        opp.party_id = party.id
    for act in db.execute(select(Activity).where(Activity.lead_id == lead.id)).scalars():
        act.party_id = party.id
    return lead


# --- Oportunidades --------------------------------------------------------------------


def create_opportunity(db: Session, *, user_id, title: str, party_id, lead_id, amount: Decimal,
                       expected_close_date: date | None, owner_user_id, notes: str | None) -> Opportunity:
    if (party_id is None) == (lead_id is None):
        raise SalesError("La oportunidad es de un cliente o de un prospecto (exactamente uno).")
    if party_id is not None:
        _customer(db, party_id)
    else:
        _open_lead(db, lead_id)
    stage = OpportunityStage.NEW
    opp = Opportunity(
        id=uuid.uuid4(), number=next_number(db, "OPPORTUNITY"), title=title, party_id=party_id, lead_id=lead_id,
        stage=stage, amount=amount, currency=_currency(db).code, probability=DEFAULT_PROBABILITY[stage],
        expected_close_date=expected_close_date, owner_user_id=owner_user_id or user_id, notes=notes,
        created_by_user_id=user_id,
    )
    db.add(opp)
    db.flush()
    return opp


def _lock_open(db: Session, opp_id: uuid.UUID) -> Opportunity:
    opp = _lock(db, Opportunity, opp_id)
    if opp.stage not in OPEN_STAGES:
        raise InvalidTransition("La oportunidad esta cerrada (ganada o perdida) y no se modifica.")
    return opp


def update_opportunity(db: Session, opp_id: uuid.UUID, changes: dict) -> Opportunity:
    opp = _lock_open(db, opp_id)
    for k, v in changes.items():
        setattr(opp, k, v)
    return opp


def move_stage(db: Session, opp_id: uuid.UUID, stage: OpportunityStage, probability: int | None) -> Opportunity:
    if stage not in OPEN_STAGES:
        raise SalesError("Para cerrar usar 'ganada' o 'perdida'.")
    opp = _lock_open(db, opp_id)
    opp.stage = stage
    opp.probability = DEFAULT_PROBABILITY[stage] if probability is None else probability
    return opp


def win(db: Session, opp_id: uuid.UUID) -> Opportunity:
    opp = _lock_open(db, opp_id)
    if opp.lead_id is not None:
        raise InvalidTransition("Converti el prospecto en cliente antes de ganar la oportunidad.")
    if opp.amount <= 0:
        raise SalesError("Una oportunidad ganada tiene que tener monto.")
    opp.stage = OpportunityStage.WON
    opp.probability = 100
    opp.closed_at = _now()
    return opp


def lose(db: Session, opp_id: uuid.UUID, reason: str) -> Opportunity:
    opp = _lock_open(db, opp_id)
    opp.stage = OpportunityStage.LOST
    opp.probability = 0
    opp.lost_reason = reason
    opp.closed_at = _now()
    return opp


# --- Actividades ----------------------------------------------------------------------


def create_activity(db: Session, *, user_id, kind: ActivityKind, subject: str, notes: str | None, party_id,
                    lead_id, opportunity_id, due_at: datetime | None, owner_user_id) -> Activity:
    if party_id is None and lead_id is None and opportunity_id is None:
        raise SalesError("La actividad tiene que ser de un cliente, prospecto u oportunidad.")
    if party_id is not None and db.get(Party, party_id) is None:
        raise SalesError("Tercero inexistente.")
    if lead_id is not None and db.get(Lead, lead_id) is None:
        raise SalesError("Prospecto inexistente.")
    if opportunity_id is not None:
        opp = db.get(Opportunity, opportunity_id)
        if opp is None:
            raise SalesError("Oportunidad inexistente.")
        # Se hereda el cliente/prospecto de la oportunidad para que la
        # actividad aparezca tambien en su historial.
        party_id = party_id or opp.party_id
        lead_id = lead_id or opp.lead_id
    act = Activity(id=uuid.uuid4(), kind=kind, subject=subject, notes=notes, party_id=party_id, lead_id=lead_id,
                   opportunity_id=opportunity_id, due_at=due_at, owner_user_id=owner_user_id or user_id,
                   created_by_user_id=user_id,
                   # Una nota es un registro de algo que ya paso.
                   done_at=_now() if kind == ActivityKind.NOTE else None)
    db.add(act)
    db.flush()
    return act


def complete_activity(db: Session, activity_id: uuid.UUID, notes: str | None) -> Activity:
    act = _lock(db, Activity, activity_id)
    if act.done_at is not None:
        raise InvalidTransition("La actividad ya estaba completada.")
    act.done_at = _now()
    if notes:
        act.notes = f"{act.notes}\n{notes}" if act.notes else notes
    return act


# --- Embudo ---------------------------------------------------------------------------


@dataclass
class StageSummary:
    stage: OpportunityStage
    count: int
    amount: Decimal
    weighted: Decimal


def pipeline(db: Session, owner_user_id=None) -> dict:
    stmt = select(Opportunity.stage, func.count(), func.coalesce(func.sum(Opportunity.amount), 0),
                  func.coalesce(func.sum(Opportunity.amount * Opportunity.probability / 100), 0)).group_by(Opportunity.stage)
    if owner_user_id is not None:
        stmt = stmt.where(Opportunity.owner_user_id == owner_user_id)
    rows = {r[0]: r for r in db.execute(stmt)}
    stages = [StageSummary(s, rows[s][1] if s in rows else 0, Decimal(rows[s][2]) if s in rows else Decimal(0),
                           Decimal(rows[s][3]).quantize(Decimal("0.01")) if s in rows else Decimal(0))
              for s in OpportunityStage]
    open_ = [s for s in stages if s.stage in OPEN_STAGES]
    won = next(s for s in stages if s.stage == OpportunityStage.WON)
    lost = next(s for s in stages if s.stage == OpportunityStage.LOST)
    closed = won.count + lost.count
    return {
        "stages": stages,
        "open_count": sum(s.count for s in open_),
        "open_amount": sum((s.amount for s in open_), Decimal(0)),
        "forecast": sum((s.weighted for s in open_), Decimal(0)),
        "won_amount": won.amount,
        "win_rate": round(won.count * 100 / closed, 1) if closed else None,
    }
