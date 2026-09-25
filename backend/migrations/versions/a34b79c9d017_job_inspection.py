"""persist inspection on generation job

Revision ID: a34b79c9d017
Revises: f7a1c92310bd
"""

from alembic import op
import sqlalchemy as sa

revision = "a34b79c9d017"
down_revision = "f7a1c92310bd"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_jobs", sa.Column("inspection_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_jobs", "inspection_json")
