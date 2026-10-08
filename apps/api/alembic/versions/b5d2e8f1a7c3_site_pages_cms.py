"""site pages cms (draft / published / versions)

Revision ID: b5d2e8f1a7c3
Revises: 491afbcedc36
Create Date: 2026-10-08 22:40:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b5d2e8f1a7c3'
down_revision = '491afbcedc36'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'site_pages',
        sa.Column('slug', sa.String(length=40), nullable=False),
        sa.Column('draft', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('published', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('published_version', sa.Integer(), nullable=False),
        sa.Column('draft_updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('draft_updated_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['draft_updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['published_by'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('slug'),
    )
    op.create_table(
        'site_page_versions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('slug', sa.String(length=40), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['slug'], ['site_pages.slug']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', 'version', name='uq_site_page_versions_slug_version'),
    )
    op.create_index('ix_site_page_versions_slug', 'site_page_versions', ['slug'])
    # El historial publicado es evidencia de que se mostro al publico: no se
    # edita ni se borra (restaurar = copiar al borrador y volver a publicar).
    op.execute("""
        CREATE FUNCTION site_page_versions_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'site_page_versions es inmutable';
        END $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_site_page_versions_immutable BEFORE UPDATE OR DELETE ON site_page_versions
            FOR EACH ROW EXECUTE FUNCTION site_page_versions_immutable();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_site_page_versions_immutable ON site_page_versions")
    op.execute("DROP FUNCTION IF EXISTS site_page_versions_immutable()")
    op.drop_index('ix_site_page_versions_slug', table_name='site_page_versions')
    op.drop_table('site_page_versions')
    op.drop_table('site_pages')
