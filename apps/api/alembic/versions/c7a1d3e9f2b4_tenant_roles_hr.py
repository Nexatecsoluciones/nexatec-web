"""tenant roles HR and SITE_SUPERVISOR

Revision ID: c7a1d3e9f2b4
Revises: b5d2e8f1a7c3
Create Date: 2026-10-08 23:40:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'c7a1d3e9f2b4'
down_revision = 'b5d2e8f1a7c3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Aditivo (mismo criterio que a448b4209cd2). ALTER TYPE ... ADD VALUE no
    # puede usarse dentro de la misma transaccion que lo inserta, pero aca
    # solo se agrega.
    for role in ("HR", "SITE_SUPERVISOR"):
        op.execute(f"ALTER TYPE tenant_member_role ADD VALUE IF NOT EXISTS '{role}'")


def downgrade() -> None:
    # PostgreSQL no permite quitar valores de un enum: los miembros con estos
    # roles pasan a solo lectura y el tipo conserva los valores.
    op.execute("UPDATE tenant_users SET role = 'CLIENT_USER' WHERE role::text IN ('HR', 'SITE_SUPERVISOR')")
