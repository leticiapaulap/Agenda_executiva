"""Add usernames to access records without changing existing entries.

Revision ID: 0008_access_username
Revises: 0007_admin_recovery
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_access_username'
down_revision = '0007_admin_recovery'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('access_catalog', sa.Column(
        'username', sa.String(254), nullable=False, server_default=''))


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar os acessos existentes.')
