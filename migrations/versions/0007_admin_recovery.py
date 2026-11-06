"""Persist expiring, single-use operator recovery authorizations.

Revision ID: 0007_admin_recovery
Revises: 0006_reminder_center
"""
from alembic import op
import sqlalchemy as sa

revision = '0007_admin_recovery'
down_revision = '0006_reminder_center'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'admin_recovery_grants',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('target_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('token_digest', sa.String(64), nullable=True, unique=True),
        sa.Column('backup_digest', sa.String(64), nullable=False),
        sa.Column('expected_session_version', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('consumed_at', sa.DateTime(), nullable=True),
        sa.Column('revoked_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar autorizações e histórico.')
