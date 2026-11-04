"""Create hosted agenda tables.

Revision ID: 0001_create_agenda_tables
Revises:
"""
from alembic import op
import sqlalchemy as sa


revision = '0001_create_agenda_tables'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('event_date', sa.Date(), nullable=False),
        sa.Column('event_time', sa.Time(), nullable=False),
        sa.Column('category', sa.String(length=80), nullable=False, server_default='Compromisso'),
        sa.Column('location', sa.String(length=200), nullable=False, server_default=''),
        sa.Column('notes', sa.Text(), nullable=False, server_default=''),
        sa.Column('reminder_minutes', sa.Integer(), nullable=False, server_default='15'),
        sa.Column('completed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('end_time', sa.Time(), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=False, server_default='Normal'),
        sa.CheckConstraint('completed IN (0, 1)', name='ck_events_completed'),
    )
    op.create_index('ix_events_event_date_event_time', 'events', ['event_date', 'event_time'])
    op.create_table(
        'tasks',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=False, server_default=''),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('priority', sa.String(length=20), nullable=False, server_default='Normal'),
        sa.Column('completed', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('completed IN (0, 1)', name='ck_tasks_completed'),
    )
    op.create_index('ix_tasks_completed_due_date', 'tasks', ['completed', 'due_date'])
    op.create_table(
        'login_attempts',
        sa.Column('client_key', sa.String(length=64), primary_key=True),
        sa.Column('failures', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('window_started_at', sa.BigInteger(), nullable=False),
        sa.Column('locked_until', sa.BigInteger(), nullable=False, server_default='0'),
    )


def downgrade():
    raise RuntimeError(
        'Esta migração inicial não pode ser revertida automaticamente, pois isso apagaria os dados.'
    )
