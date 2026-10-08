"""Modelos que viven DENTRO de cada base de tenant (nxt_<tenant>_<sistema>_<env>),
nunca en nexatec_control. Metadata separada a proposito (TenantBase, no
app.core.db.Base): el Alembic del control plane jamas debe ver estas
tablas, y el Alembic de tenant (tenant_alembic/) jamas debe ver las del
control plane."""

from sqlalchemy.orm import DeclarativeBase


class TenantBase(DeclarativeBase):
    pass
