"""add users.token_version

Revision ID: a3c81e5d2b47
Revises: 7f46ffc9b095
Create Date: 2026-09-20 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c81e5d2b47'
down_revision = '7f46ffc9b095'
branch_labels = None
depends_on = None


def upgrade():
    # server_default fills existing rows, and lets the previous app version
    # (which never sets the column) keep inserting during a rolling deploy.
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('token_version')
