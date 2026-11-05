"""Add optional authenticated vault ciphertext and conservative corporate flag.

Revision ID: 0004_vault_passwords
Revises: 0003_access_catalog
"""
from alembic import op
import sqlalchemy as sa

revision = '0004_vault_passwords'
down_revision = '0003_access_catalog'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('access_catalog', sa.Column('password_ciphertext', sa.Text(), nullable=True))
    op.add_column('access_catalog', sa.Column('is_corporate', sa.Integer(),
                  sa.CheckConstraint('is_corporate IN (0,1)', name='ck_access_catalog_is_corporate'),
                  sa.CheckConstraint('is_corporate=0 OR password_ciphertext IS NULL',
                                     name='ck_access_catalog_password_scope'),
                  nullable=False, server_default='1'))


def downgrade():
    raise RuntimeError('Reversão automática recusada para preservar senhas criptografadas e acessos.')
