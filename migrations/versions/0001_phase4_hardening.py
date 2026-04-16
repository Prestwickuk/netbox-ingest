"""Phase 4 hardening: batch_size, rate_limit on jobs; netbox_instances table

Revision ID: 0001
Revises:
Create Date: 2026-04-16
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "netbox_instances",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("url", sa.String(500), nullable=False),
        sa.Column("token", sa.String(500), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.add_column("jobs", sa.Column("batch_size", sa.Integer(), nullable=True))
    op.add_column("jobs", sa.Column("rate_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "rate_limit")
    op.drop_column("jobs", "batch_size")
    op.drop_table("netbox_instances")
