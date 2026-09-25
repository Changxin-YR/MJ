"""shot inspection

Revision ID: f7a1c92310bd
Revises: 558ac98e991a
"""

from alembic import op
import sqlalchemy as sa

revision = "f7a1c92310bd"
down_revision = "558ac98e991a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shots", sa.Column("inspection_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("shots", "inspection_json")
