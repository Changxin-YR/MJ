"""enforce one timeline per project episode

Revision ID: c91d4e6f2a10
Revises: b2d6e82f4a11
"""

from alembic import op

revision = "c91d4e6f2a10"
down_revision = "b2d6e82f4a11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_timeline_project_episode",
        "timelines",
        ["project_id", "episode_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_timeline_project_episode", "timelines", type_="unique")
