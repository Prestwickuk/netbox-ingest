"""Encrypt saved and per-job NetBox tokens.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa

from app.security.tokens import migrate_token_columns


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "netbox_instances",
        "token",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.alter_column(
        "jobs",
        "netbox_token",
        existing_type=sa.String(length=500),
        type_=sa.Text(),
        existing_nullable=False,
    )
    migrate_token_columns(op.get_bind())


def downgrade() -> None:
    migrate_token_columns(op.get_bind(), decrypt=True)
    op.alter_column(
        "jobs",
        "netbox_token",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
    op.alter_column(
        "netbox_instances",
        "token",
        existing_type=sa.Text(),
        type_=sa.String(length=500),
        existing_nullable=False,
    )
