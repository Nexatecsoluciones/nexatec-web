"""crm leads, opportunities and activities

Revision ID: 7c1e5a92d3f4
Revises: 0b4e58d9d0a6
Create Date: 2026-10-08 22:20:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '7c1e5a92d3f4'
down_revision = '0b4e58d9d0a6'
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        'crm_leads',
        sa.Column('id', _UUID, nullable=False),
        sa.Column('contact_name', sa.String(length=200), nullable=False),
        sa.Column('company_name', sa.String(length=200), nullable=True),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=40), nullable=True),
        sa.Column('source', sa.String(length=60), nullable=True),
        sa.Column('status', sa.Enum('OPEN', 'CONVERTED', 'DISCARDED', name='crm_lead_status'), nullable=False),
        sa.Column('owner_user_id', _UUID, nullable=True),
        sa.Column('party_id', _UUID, nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('discard_reason', sa.Text(), nullable=True),
        sa.Column('created_by_user_id', _UUID, nullable=True),
        sa.Column('created_at', _TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', _TS, server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("status <> 'CONVERTED' OR party_id IS NOT NULL", name='ck_crm_leads_converted_has_party'),
        sa.CheckConstraint('email IS NOT NULL OR phone IS NOT NULL', name='ck_crm_leads_has_contact'),
        sa.ForeignKeyConstraint(['party_id'], ['parties.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_crm_leads_status', 'crm_leads', ['status'])
    op.create_index('ix_crm_leads_owner_user_id', 'crm_leads', ['owner_user_id'])

    op.create_table(
        'crm_opportunities',
        sa.Column('id', _UUID, nullable=False),
        sa.Column('number', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('party_id', _UUID, nullable=True),
        sa.Column('lead_id', _UUID, nullable=True),
        sa.Column('stage', sa.Enum('NEW', 'QUALIFIED', 'PROPOSAL', 'NEGOTIATION', 'WON', 'LOST',
                                   name='crm_opportunity_stage'), nullable=False),
        sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('probability', sa.Integer(), nullable=False),
        sa.Column('expected_close_date', sa.Date(), nullable=True),
        sa.Column('owner_user_id', _UUID, nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('lost_reason', sa.Text(), nullable=True),
        sa.Column('closed_at', _TS, nullable=True),
        sa.Column('created_by_user_id', _UUID, nullable=True),
        sa.Column('created_at', _TS, server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', _TS, server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('(party_id IS NULL) <> (lead_id IS NULL)', name='ck_crm_opportunities_one_owner'),
        sa.CheckConstraint('amount >= 0', name='ck_crm_opportunities_amount_non_negative'),
        sa.CheckConstraint('probability BETWEEN 0 AND 100', name='ck_crm_opportunities_probability_range'),
        sa.CheckConstraint("stage <> 'LOST' OR lost_reason IS NOT NULL", name='ck_crm_opportunities_lost_has_reason'),
        sa.CheckConstraint("(stage IN ('WON', 'LOST')) = (closed_at IS NOT NULL)", name='ck_crm_opportunities_closed_at'),
        sa.ForeignKeyConstraint(['currency'], ['currencies.code']),
        sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id']),
        sa.ForeignKeyConstraint(['party_id'], ['parties.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('number'),
    )
    op.create_index('ix_crm_opportunities_stage', 'crm_opportunities', ['stage'])
    op.create_index('ix_crm_opportunities_party_id', 'crm_opportunities', ['party_id'])
    op.create_index('ix_crm_opportunities_lead_id', 'crm_opportunities', ['lead_id'])
    op.create_index('ix_crm_opportunities_owner_user_id', 'crm_opportunities', ['owner_user_id'])

    op.create_table(
        'crm_activities',
        sa.Column('id', _UUID, nullable=False),
        sa.Column('kind', sa.Enum('CALL', 'MEETING', 'EMAIL', 'WHATSAPP', 'TASK', 'NOTE', name='crm_activity_kind'), nullable=False),
        sa.Column('subject', sa.String(length=200), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('party_id', _UUID, nullable=True),
        sa.Column('lead_id', _UUID, nullable=True),
        sa.Column('opportunity_id', _UUID, nullable=True),
        sa.Column('due_at', _TS, nullable=True),
        sa.Column('done_at', _TS, nullable=True),
        sa.Column('owner_user_id', _UUID, nullable=True),
        sa.Column('created_by_user_id', _UUID, nullable=True),
        sa.Column('created_at', _TS, server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint('num_nonnulls(party_id, lead_id, opportunity_id) >= 1', name='ck_crm_activities_has_subject'),
        sa.ForeignKeyConstraint(['lead_id'], ['crm_leads.id']),
        sa.ForeignKeyConstraint(['opportunity_id'], ['crm_opportunities.id']),
        sa.ForeignKeyConstraint(['party_id'], ['parties.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_crm_activities_party_id', 'crm_activities', ['party_id'])
    op.create_index('ix_crm_activities_lead_id', 'crm_activities', ['lead_id'])
    op.create_index('ix_crm_activities_opportunity_id', 'crm_activities', ['opportunity_id'])
    op.create_index('ix_crm_activities_due_at', 'crm_activities', ['due_at'])
    op.create_index('ix_crm_activities_owner_user_id', 'crm_activities', ['owner_user_id'])

    seqs = sa.table("document_sequences", sa.column("code"), sa.column("prefix"), sa.column("next_value"))
    op.bulk_insert(seqs, [{"code": "OPPORTUNITY", "prefix": "OP-", "next_value": 1}])


def downgrade() -> None:
    op.execute("DELETE FROM document_sequences WHERE code = 'OPPORTUNITY'")
    op.drop_table('crm_activities')
    op.drop_table('crm_opportunities')
    op.drop_table('crm_leads')
    op.execute("DROP TYPE IF EXISTS crm_activity_kind")
    op.execute("DROP TYPE IF EXISTS crm_opportunity_stage")
    op.execute("DROP TYPE IF EXISTS crm_lead_status")
