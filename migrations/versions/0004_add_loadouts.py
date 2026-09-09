"""Add loadouts table

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text(
        "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
    ))}
    if "loadouts" in tables:
        return
    op.create_table(
        "loadouts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("manufacturer", sa.String(255), nullable=False),
        sa.Column("device_type", sa.String(255), nullable=False),
        sa.Column("bays", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("loadouts")
