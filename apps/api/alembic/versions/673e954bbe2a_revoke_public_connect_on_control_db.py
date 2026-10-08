"""revoke public connect on control db

Revision ID: 673e954bbe2a
Revises: 3edabb706dee
Create Date: 2026-10-08 03:55:16.462830

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '673e954bbe2a'
down_revision = '3edabb706dee'
branch_labels = None
depends_on = None


# PostgreSQL da CONNECT y TEMPORARY a PUBLIC en toda base nueva: sin esto,
# el rol de cualquier tenant (nxt_*) podia conectarse a la base del control
# plane (no leer tablas, pero si abrir sesiones, crear tablas temporales y
# leer el catalogo). El dueno (nexatec_app) conserva sus privilegios.
# current_database() en vez de un nombre fijo: aplica igual en dev/staging.


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            EXECUTE format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            EXECUTE format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO PUBLIC', current_database());
        END $$;
    """)
