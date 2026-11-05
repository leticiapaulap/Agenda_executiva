"""Add persistent per-user manual reminders and alert state.

Revision ID: 0006_reminder_center
Revises: 0005_users_permissions
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_reminder_center'
down_revision = '0005_users_permissions'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'manual_reminders',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('due_at', sa.DateTime(), nullable=False),
        sa.Column('priority', sa.String(20), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('completed', sa.Boolean(), nullable=False),
        sa.Column('snoozed_until', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.CheckConstraint("priority IN ('Urgente','Alta','Normal','Baixa')",
                           name='ck_manual_reminders_priority'),
    )
    op.create_index('ix_manual_reminders_owner_due', 'manual_reminders', ['owner_id', 'due_at'])
    op.create_table(
        'reminder_states',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('item_type', sa.String(10), nullable=False),
        sa.Column('item_id', sa.String(120), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False),
        sa.Column('read_at', sa.DateTime(), nullable=True),
        sa.Column('snoozed_until', sa.DateTime(), nullable=True),
        sa.CheckConstraint("item_type IN ('event','task','bill')", name='ck_reminder_states_type'),
        sa.UniqueConstraint('user_id', 'item_type', 'item_id', name='uq_reminder_states_item'),
    )
    op.create_index('ix_reminder_states_user', 'reminder_states', ['user_id'])


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar lembretes e seu histórico.')
