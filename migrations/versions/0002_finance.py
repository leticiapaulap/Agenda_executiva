"""Add bills, monthly series, immutable payment snapshots and reminder ledger.

Revision ID: 0002_finance
Revises: 0001_create_agenda_tables
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_finance'
down_revision = '0001_create_agenda_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'bill_series',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('responsible', sa.String(200), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('anchor_day', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=False),
        sa.Column('active', sa.Integer(), nullable=False),
        sa.CheckConstraint('amount_cents >= 0', name='ck_bill_series_amount'),
        sa.CheckConstraint('anchor_day BETWEEN 1 AND 31', name='ck_bill_series_day'),
        sa.CheckConstraint('active IN (0,1)', name='ck_bill_series_active'),
    )
    op.create_table(
        'bills',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('responsible', sa.String(200), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('recurrence', sa.String(10), nullable=False),
        sa.Column('series_id', sa.String(36), sa.ForeignKey('bill_series.id'), nullable=True),
        sa.Column('occurrence_month', sa.String(7), nullable=True),
        sa.Column('anchor_day', sa.Integer(), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=True),
        sa.Column('receipt_id', sa.String(128), nullable=False, server_default=''),
        sa.Column('deleted', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('amount_cents >= 0', name='ck_bills_amount'),
        sa.CheckConstraint('anchor_day BETWEEN 1 AND 31', name='ck_bills_day'),
        sa.CheckConstraint('deleted IN (0,1)', name='ck_bills_deleted'),
        sa.CheckConstraint("recurrence IN ('Única','Mensal')", name='ck_bills_recurrence'),
        sa.UniqueConstraint('series_id', 'occurrence_month', name='uq_bills_series_month'),
    )
    op.create_index('ix_bills_due_date', 'bills', ['due_date'])
    op.create_table(
        'bill_payments',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('bill_id', sa.String(36), sa.ForeignKey('bills.id'), nullable=False, unique=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('amount_cents', sa.BigInteger(), nullable=False),
        sa.Column('due_date', sa.Date(), nullable=False),
        sa.Column('responsible', sa.String(200), nullable=False),
        sa.Column('paid_on', sa.Date(), nullable=False),
        sa.CheckConstraint('amount_cents >= 0', name='ck_bill_payments_amount'),
    )
    op.create_table(
        'bill_reminder_log',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('bill_id', sa.String(36), sa.ForeignKey('bills.id'), nullable=False),
        sa.Column('days_before', sa.Integer(), nullable=False),
        sa.Column('reminder_date', sa.Date(), nullable=False),
        sa.Column('notified_at', sa.String(32), nullable=False),
        sa.CheckConstraint('days_before IN (7,3,1)', name='ck_bill_reminder_days'),
    )


def downgrade():
    raise RuntimeError('Reversão automática desativada para preservar contas e histórico de pagamentos.')
