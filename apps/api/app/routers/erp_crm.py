"""CRM: prospectos, oportunidades, actividades y embudo. La logica esta en
app/services/crm.py."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from app.core.audit import log_audit
from app.security.erp_context import ErpContext, require
from app.services import crm
from app.services.sales import SalesError
from app.tenant_models.core import Party
from app.tenant_models.crm import Activity, ActivityKind, Lead, LeadStatus, Opportunity, OpportunityStage

router = APIRouter(prefix="/api/erp/{system_access_id}/crm", tags=["erp", "crm"])

MAX_PAGE = 200


class LeadIn(BaseModel):
    contact_name: str = Field(min_length=2, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)


class LeadUpdate(BaseModel):
    contact_name: str | None = Field(default=None, min_length=2, max_length=200)
    company_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)
    source: str | None = Field(default=None, max_length=60)
    notes: str | None = Field(default=None, max_length=4000)


class LeadOut(BaseModel):
    id: uuid.UUID
    contact_name: str
    company_name: str | None
    email: str | None
    phone: str | None
    source: str | None
    status: LeadStatus
    party_id: uuid.UUID | None
    notes: str | None
    discard_reason: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class LeadPage(BaseModel):
    total: int
    items: list[LeadOut]


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=1000)


class OpportunityIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    party_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    amount: Decimal = Field(default=Decimal("0"), ge=0, max_digits=18, decimal_places=2)
    expected_close_date: date | None = None
    notes: str | None = Field(default=None, max_length=4000)


class OpportunityUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=3, max_length=200)
    amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    expected_close_date: date | None = None
    probability: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = Field(default=None, max_length=4000)


class StageIn(BaseModel):
    stage: OpportunityStage
    probability: int | None = Field(default=None, ge=0, le=100)


class OpportunityOut(BaseModel):
    id: uuid.UUID
    number: str
    title: str
    party_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    account_name: str | None = None
    stage: OpportunityStage
    amount: Decimal
    currency: str
    probability: int
    expected_close_date: date | None
    notes: str | None
    lost_reason: str | None
    closed_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class OpportunityPage(BaseModel):
    total: int
    items: list[OpportunityOut]


class ActivityIn(BaseModel):
    kind: ActivityKind
    subject: str = Field(min_length=2, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    party_id: uuid.UUID | None = None
    lead_id: uuid.UUID | None = None
    opportunity_id: uuid.UUID | None = None
    due_at: datetime | None = None


class CompleteIn(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)


class ActivityOut(BaseModel):
    id: uuid.UUID
    kind: ActivityKind
    subject: str
    notes: str | None
    party_id: uuid.UUID | None
    lead_id: uuid.UUID | None
    opportunity_id: uuid.UUID | None
    due_at: datetime | None
    done_at: datetime | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class ActivityPage(BaseModel):
    total: int
    items: list[ActivityOut]


class StageSummaryOut(BaseModel):
    stage: OpportunityStage
    count: int
    amount: Decimal
    weighted: Decimal


class PipelineOut(BaseModel):
    stages: list[StageSummaryOut]
    open_count: int
    open_amount: Decimal
    forecast: Decimal
    won_amount: Decimal
    win_rate: float | None
    overdue_activities: int


def _run(ctx: ErpContext, action: str, fn, resource: str):
    try:
        obj = fn()
        ctx.db.commit()
    except SalesError as exc:
        ctx.db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message)
    except IntegrityError:
        ctx.db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Conflicto o dato duplicado.")
    ctx.db.refresh(obj)
    meta = {k: getattr(obj, k).value for k in ("status", "stage", "kind") if hasattr(obj, k)}
    if hasattr(obj, "number"):
        meta["number"] = obj.number
    log_audit(ctx.control_db, actor_user_id=ctx.user.id, tenant_id=ctx.tenant_id, action=action,
              resource=f"{resource}:{obj.id}", metadata=meta)
    ctx.control_db.commit()
    return obj


def _page(ctx: ErpContext, stmt, order_by, limit: int, offset: int):
    total = ctx.db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    return total, ctx.db.execute(stmt.order_by(*order_by).limit(limit).offset(offset)).scalars().all()


def _get(ctx: ErpContext, model, obj_id: uuid.UUID):
    obj = ctx.db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recurso no encontrado.")
    return obj


def _with_names(ctx: ErpContext, opps: list[Opportunity]) -> list[OpportunityOut]:
    party_ids = {o.party_id for o in opps if o.party_id}
    lead_ids = {o.lead_id for o in opps if o.lead_id}
    parties = dict(ctx.db.execute(select(Party.id, Party.legal_name).where(Party.id.in_(party_ids))).all()) if party_ids else {}
    leads = {i: c or n for i, c, n in ctx.db.execute(
        select(Lead.id, Lead.company_name, Lead.contact_name).where(Lead.id.in_(lead_ids))).all()} if lead_ids else {}
    out = []
    for o in opps:
        item = OpportunityOut.model_validate(o)
        item.account_name = parties.get(o.party_id) if o.party_id else leads.get(o.lead_id)
        out.append(item)
    return out


# --- Prospectos -----------------------------------------------------------------------


@router.get("/leads", response_model=LeadPage)
def list_leads(
    ctx: ErpContext = Depends(require("crm:read")),
    q: str | None = Query(default=None, max_length=100),
    lead_status: LeadStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Lead)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Lead.contact_name.ilike(like), Lead.company_name.ilike(like), Lead.email.ilike(like),
                              Lead.phone.ilike(like)))
    if lead_status:
        stmt = stmt.where(Lead.status == lead_status)
    total, items = _page(ctx, stmt, [Lead.created_at.desc()], limit, offset)
    return {"total": total, "items": items}


@router.post("/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(payload: LeadIn, ctx: ErpContext = Depends(require("crm:write"))):
    return _run(ctx, "ERP_CRM_LEAD_CREATED", lambda: crm.create_lead(
        ctx.db, user_id=ctx.user.id, owner_user_id=None, **payload.model_dump()), "crm_lead")


@router.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: uuid.UUID, ctx: ErpContext = Depends(require("crm:read"))):
    return _get(ctx, Lead, lead_id)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(lead_id: uuid.UUID, payload: LeadUpdate, ctx: ErpContext = Depends(require("crm:write"))):
    return _run(ctx, "ERP_CRM_LEAD_UPDATED",
                lambda: crm.update_lead(ctx.db, lead_id, payload.model_dump(exclude_unset=True)), "crm_lead")


@router.post("/leads/{lead_id}/discard", response_model=LeadOut)
def discard_lead(lead_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("crm:write"))):
    return _run(ctx, "ERP_CRM_LEAD_DISCARDED", lambda: crm.discard_lead(ctx.db, lead_id, payload.reason), "crm_lead")


@router.post("/leads/{lead_id}/convert", response_model=LeadOut)
def convert_lead(lead_id: uuid.UUID, ctx: ErpContext = Depends(require("crm:write"))):
    # Crea un tercero: ademas del CRM hace falta poder dar de alta clientes.
    if not ctx.can("parties:write"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu rol no puede dar de alta clientes.")
    return _run(ctx, "ERP_CRM_LEAD_CONVERTED", lambda: crm.convert_lead(ctx.db, lead_id), "crm_lead")


# --- Oportunidades --------------------------------------------------------------------


@router.get("/opportunities", response_model=OpportunityPage)
def list_opportunities(
    ctx: ErpContext = Depends(require("crm:read")),
    q: str | None = Query(default=None, max_length=100),
    stage: OpportunityStage | None = None,
    open_only: bool = False,
    party_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Opportunity)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Opportunity.title.ilike(like), Opportunity.number.ilike(like)))
    if stage:
        stmt = stmt.where(Opportunity.stage == stage)
    if open_only:
        stmt = stmt.where(Opportunity.stage.in_(crm.OPEN_STAGES))
    if party_id:
        stmt = stmt.where(Opportunity.party_id == party_id)
    if lead_id:
        stmt = stmt.where(Opportunity.lead_id == lead_id)
    total, items = _page(ctx, stmt, [Opportunity.expected_close_date.asc().nulls_last(), Opportunity.created_at.desc()],
                         limit, offset)
    return {"total": total, "items": _with_names(ctx, list(items))}


@router.post("/opportunities", response_model=OpportunityOut, status_code=status.HTTP_201_CREATED)
def create_opportunity(payload: OpportunityIn, ctx: ErpContext = Depends(require("crm:write"))):
    opp = _run(ctx, "ERP_CRM_OPPORTUNITY_CREATED", lambda: crm.create_opportunity(
        ctx.db, user_id=ctx.user.id, owner_user_id=None, **payload.model_dump()), "crm_opportunity")
    return _with_names(ctx, [opp])[0]


@router.get("/opportunities/{opp_id}", response_model=OpportunityOut)
def get_opportunity(opp_id: uuid.UUID, ctx: ErpContext = Depends(require("crm:read"))):
    return _with_names(ctx, [_get(ctx, Opportunity, opp_id)])[0]


@router.patch("/opportunities/{opp_id}", response_model=OpportunityOut)
def update_opportunity(opp_id: uuid.UUID, payload: OpportunityUpdate, ctx: ErpContext = Depends(require("crm:write"))):
    opp = _run(ctx, "ERP_CRM_OPPORTUNITY_UPDATED",
               lambda: crm.update_opportunity(ctx.db, opp_id, payload.model_dump(exclude_unset=True)), "crm_opportunity")
    return _with_names(ctx, [opp])[0]


@router.post("/opportunities/{opp_id}/stage", response_model=OpportunityOut)
def move_stage(opp_id: uuid.UUID, payload: StageIn, ctx: ErpContext = Depends(require("crm:write"))):
    opp = _run(ctx, "ERP_CRM_OPPORTUNITY_STAGE",
               lambda: crm.move_stage(ctx.db, opp_id, payload.stage, payload.probability), "crm_opportunity")
    return _with_names(ctx, [opp])[0]


@router.post("/opportunities/{opp_id}/win", response_model=OpportunityOut)
def win(opp_id: uuid.UUID, ctx: ErpContext = Depends(require("crm:write"))):
    opp = _run(ctx, "ERP_CRM_OPPORTUNITY_WON", lambda: crm.win(ctx.db, opp_id), "crm_opportunity")
    return _with_names(ctx, [opp])[0]


@router.post("/opportunities/{opp_id}/lose", response_model=OpportunityOut)
def lose(opp_id: uuid.UUID, payload: ReasonIn, ctx: ErpContext = Depends(require("crm:write"))):
    opp = _run(ctx, "ERP_CRM_OPPORTUNITY_LOST", lambda: crm.lose(ctx.db, opp_id, payload.reason), "crm_opportunity")
    return _with_names(ctx, [opp])[0]


# --- Actividades ----------------------------------------------------------------------


@router.get("/activities", response_model=ActivityPage)
def list_activities(
    ctx: ErpContext = Depends(require("crm:read")),
    pending: bool | None = None,
    mine: bool = False,
    party_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    opportunity_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=MAX_PAGE),
    offset: int = Query(default=0, ge=0),
):
    stmt = select(Activity)
    if pending is True:
        stmt = stmt.where(Activity.done_at.is_(None))
    elif pending is False:
        stmt = stmt.where(Activity.done_at.is_not(None))
    if mine:
        stmt = stmt.where(Activity.owner_user_id == ctx.user.id)
    for col, val in ((Activity.party_id, party_id), (Activity.lead_id, lead_id), (Activity.opportunity_id, opportunity_id)):
        if val:
            stmt = stmt.where(col == val)
    order = [Activity.due_at.asc().nulls_last(), Activity.created_at.desc()] if pending else [Activity.created_at.desc()]
    total, items = _page(ctx, stmt, order, limit, offset)
    return {"total": total, "items": items}


@router.post("/activities", response_model=ActivityOut, status_code=status.HTTP_201_CREATED)
def create_activity(payload: ActivityIn, ctx: ErpContext = Depends(require("crm:write"))):
    return _run(ctx, "ERP_CRM_ACTIVITY_CREATED", lambda: crm.create_activity(
        ctx.db, user_id=ctx.user.id, owner_user_id=None, **payload.model_dump()), "crm_activity")


@router.post("/activities/{activity_id}/complete", response_model=ActivityOut)
def complete_activity(activity_id: uuid.UUID, payload: CompleteIn, ctx: ErpContext = Depends(require("crm:write"))):
    return _run(ctx, "ERP_CRM_ACTIVITY_DONE",
                lambda: crm.complete_activity(ctx.db, activity_id, payload.notes), "crm_activity")


# --- Embudo ---------------------------------------------------------------------------


@router.get("/pipeline", response_model=PipelineOut)
def pipeline(ctx: ErpContext = Depends(require("crm:read")), mine: bool = False):
    data = crm.pipeline(ctx.db, ctx.user.id if mine else None)
    overdue = select(func.count()).select_from(Activity).where(
        Activity.done_at.is_(None), Activity.due_at < datetime.now(timezone.utc))
    if mine:
        overdue = overdue.where(Activity.owner_user_id == ctx.user.id)
    data["overdue_activities"] = ctx.db.execute(overdue).scalar_one()
    data["stages"] = [StageSummaryOut(**s.__dict__) for s in data["stages"]]
    return data
