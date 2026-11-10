"""Allow authorized corporate passwords in the existing web vault.

Revision ID: 0009_corporate_passwords
Revises: 0008_access_username
"""
from alembic import op
import sqlalchemy as sa

revision = '0009_corporate_passwords'
down_revision = '0008_access_username'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name == 'postgresql':
        # PostgreSQL changes only the policy constraint: no table rebuild,
        # row updates, credential conversion or encryption-key rotation.
        op.drop_constraint('ck_access_catalog_password_scope', 'access_catalog', type_='check')
        return

    # SQLite is test-only for the web. Explicit metadata avoids its reflection
    # ambiguity for CHECK clauses added inline by revision 0004, followed by 0008.
    previous = sa.Table(
        'access_catalog', sa.MetaData(),
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('url', sa.String(1000), nullable=False),
        sa.Column('category', sa.String(80), nullable=False),
        sa.Column('notes', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.String(40), nullable=False),
        sa.Column('password_ciphertext', sa.Text(), nullable=True),
        sa.Column('is_corporate', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('username', sa.String(254), nullable=False, server_default=''),
        sa.CheckConstraint('is_corporate IN (0,1)', name='ck_access_catalog_is_corporate'),
        sa.CheckConstraint('is_corporate=0 OR password_ciphertext IS NULL',
                           name='ck_access_catalog_password_scope'),
    )
    with op.batch_alter_table('access_catalog', copy_from=previous) as batch:
        batch.drop_constraint('ck_access_catalog_password_scope', type_='check')


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar senhas corporativas existentes.')
