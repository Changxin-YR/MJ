"""store structured automatic production runs

Revision ID: c1a7f0e2b4d9
Revises: b2d6e82f4a11
"""

from alembic import op
import sqlalchemy as sa


revision = "c1a7f0e2b4d9"
down_revision = "b2d6e82f4a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_runs", sa.Column("production_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "production_json")
