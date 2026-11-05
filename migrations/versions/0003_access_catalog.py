"""Add access directory without credential storage.

Revision ID: 0003_access_catalog
Revises: 0002_finance
"""
from alembic import op
import sqlalchemy as sa

revision = '0003_access_catalog'
down_revision = '0002_finance'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('access_catalog',
                    sa.Column('id', sa.String(36), primary_key=True),
                    sa.Column('name', sa.String(200), nullable=False),
                    sa.Column('url', sa.String(1000), nullable=False),
                    sa.Column('category', sa.String(80), nullable=False),
                    sa.Column('notes', sa.Text(), nullable=False),
                    sa.Column('updated_at', sa.String(40), nullable=False))


def downgrade():
    raise RuntimeError('Reversão automática recusada: preserve os dados do catálogo.')
