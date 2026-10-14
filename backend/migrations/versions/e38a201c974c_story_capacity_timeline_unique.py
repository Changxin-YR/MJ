"""Expand story capacity and enforce one timeline per episode."""
import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "e38a201c974c"
down_revision = "c1a7f0e2b4d9"
branch_labels = None
depends_on = None


def upgrade():
    if not context.is_offline_mode():
        duplicate = op.get_bind().execute(sa.text("SELECT episode_id FROM timelines GROUP BY workspace_id, project_id, episode_id HAVING COUNT(*) > 1 LIMIT 1")).first()
        if duplicate:
            raise RuntimeError(f"Resolve duplicate timelines for episode {duplicate[0]} before migrating; no records were removed")
    op.alter_column("story_sources", "content", existing_type=sa.Text(), type_=MEDIUMTEXT(), existing_nullable=False)
    op.create_unique_constraint("uq_timeline_episode", "timelines", ["workspace_id", "project_id", "episode_id"])


def downgrade():
    if not context.is_offline_mode():
        oversized = op.get_bind().execute(sa.text("SELECT id FROM story_sources WHERE OCTET_LENGTH(content) > 65535 LIMIT 1")).first()
        if oversized:
            raise RuntimeError("Cannot downgrade while a story exceeds TEXT capacity")
    op.drop_constraint("uq_timeline_episode", "timelines", type_="unique")
    op.alter_column("story_sources", "content", existing_type=MEDIUMTEXT(), type_=sa.Text(), existing_nullable=False)
