"""Persistent users, revocable sessions and administrative audit.

Revision ID: 0005_users_permissions
Revises: 0004_vault_passwords
"""
from alembic import op
import sqlalchemy as sa

revision = '0005_users_permissions'
down_revision = '0004_vault_passwords'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('users',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('email', sa.String(254), nullable=False, unique=True),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('role', sa.String(20), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('session_version', sa.Integer(), nullable=False),
        sa.Column('agenda_access', sa.Boolean(), nullable=False),
        sa.Column('finance_access', sa.Boolean(), nullable=False),
        sa.Column('vault_access', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_login', sa.DateTime(), nullable=True),
        sa.CheckConstraint("role IN ('admin','manager','reader')", name='ck_users_role'),
        sa.CheckConstraint('session_version >= 1', name='ck_users_session_version'),
        sa.CheckConstraint("role <> 'reader' OR NOT vault_access", name='ck_users_reader_vault'))
    op.create_index('uq_users_email_lower', 'users', [sa.text('lower(email)')], unique=True)
    op.create_table('audit_logs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('actor_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('target_user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('action', sa.String(80), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False))
    op.create_table('security_state',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('bootstrap_complete', sa.Boolean(), nullable=False),
        sa.Column('lock_version', sa.Integer(), nullable=False),
        sa.CheckConstraint('id = 1', name='ck_security_state_singleton'))
    # A marker distinct from user rows prevents deleted or renamed initial users
    # from ever being silently recreated from old deployment environment values.
    op.bulk_insert(sa.table('security_state', sa.column('id', sa.Integer()),
                           sa.column('bootstrap_complete', sa.Boolean()),
                           sa.column('lock_version', sa.Integer())),
                   [{'id': 1, 'bootstrap_complete': False, 'lock_version': 0}])
    if op.get_context().dialect.name == 'sqlite':
        # Inline REFERENCES is supported by SQLite ADD COLUMN and avoids table
        # recreation. Existing records retain their IDs and have owner_id NULL.
        op.execute('ALTER TABLE events ADD COLUMN owner_id VARCHAR(36) CONSTRAINT fk_events_owner_id_users REFERENCES users(id)')
        op.execute('ALTER TABLE tasks ADD COLUMN owner_id VARCHAR(36) CONSTRAINT fk_tasks_owner_id_users REFERENCES users(id)')
    else:
        op.add_column('events', sa.Column('owner_id', sa.String(36),
                      sa.ForeignKey('users.id', name='fk_events_owner_id_users'), nullable=True))
        op.add_column('tasks', sa.Column('owner_id', sa.String(36),
                      sa.ForeignKey('users.id', name='fk_tasks_owner_id_users'), nullable=True))


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar usuários, auditoria e dados existentes.')
