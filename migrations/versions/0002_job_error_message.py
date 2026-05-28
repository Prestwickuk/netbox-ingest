"""Add error_message column to jobs

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    cols = {row[0] for row in conn.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='jobs'"
    ))}
    if "error_message" not in cols:
        op.add_column("jobs", sa.Column("error_message", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "error_message")
