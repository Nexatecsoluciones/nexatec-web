"""erp functional member roles

Revision ID: a448b4209cd2
Revises: 673e954bbe2a
Create Date: 2026-10-08 10:52:03.624967

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a448b4209cd2'
down_revision = '673e954bbe2a'
branch_labels = None
depends_on = None


NEW_ROLES = ("MANAGER", "FINANCE", "SALES", "PURCHASING", "WAREHOUSE", "ACCOUNTANT", "AUDITOR")


def upgrade() -> None:
    # Aditivo: PostgreSQL permite agregar valores a un enum sin reescribir la tabla.
    for role in NEW_ROLES:
        op.execute(f"ALTER TYPE tenant_member_role ADD VALUE IF NOT EXISTS '{role}'")


def downgrade() -> None:
    # PostgreSQL no permite quitar valores de un enum. Se degrada a los dos
    # roles historicos y se recrea el tipo.
    op.execute("UPDATE tenant_users SET role = 'CLIENT_USER' WHERE role::text NOT IN ('CLIENT_ADMIN', 'CLIENT_USER')")
    op.execute("ALTER TYPE tenant_member_role RENAME TO tenant_member_role_old")
    op.execute("CREATE TYPE tenant_member_role AS ENUM ('CLIENT_ADMIN', 'CLIENT_USER')")
    op.execute("ALTER TABLE tenant_users ALTER COLUMN role TYPE tenant_member_role USING role::text::tenant_member_role")
    op.execute("DROP TYPE tenant_member_role_old")
