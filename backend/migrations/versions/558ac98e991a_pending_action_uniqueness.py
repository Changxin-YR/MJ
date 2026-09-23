"""pending action uniqueness

Revision ID: 558ac98e991a
Revises: e79da6243b7a
Create Date: 2026-09-23 00:57:01.751533
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '558ac98e991a'
down_revision: Union[str, None] = 'e79da6243b7a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint('uq_pending_action_run_action_target', 'pending_actions', ['agent_run_id', 'action', 'target_id'])


def downgrade() -> None:
    op.drop_constraint('uq_pending_action_run_action_target', 'pending_actions', type_='unique')
