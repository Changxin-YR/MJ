"""Persist provider callback nonces for replay protection.

Revision ID: b2d6e82f4a11
Revises: a34b79c9d017
"""

from alembic import op
import sqlalchemy as sa

revision = "b2d6e82f4a11"
down_revision = "a34b79c9d017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_callback_receipts",
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("nonce", sa.String(128), nullable=False),
        sa.Column("generation_job_id", sa.String(36), sa.ForeignKey("generation_jobs.id"), nullable=False),
        sa.Column("remote_job_id", sa.String(160), nullable=False),
        sa.Column("received_status", sa.String(30), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("workspace_id", sa.String(36), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=False),
        sa.UniqueConstraint("provider", "nonce", name="uq_provider_callback_nonce"),
    )
    op.create_index("ix_provider_callback_receipts_workspace_id", "provider_callback_receipts", ["workspace_id"])
    op.create_index("ix_provider_callback_receipts_project_id", "provider_callback_receipts", ["project_id"])


def downgrade() -> None:
    op.drop_index("ix_provider_callback_receipts_project_id", table_name="provider_callback_receipts")
    op.drop_index("ix_provider_callback_receipts_workspace_id", table_name="provider_callback_receipts")
    op.drop_table("provider_callback_receipts")
