"""version and constrain character relationships

Revision ID: e21a7c4b9f31
Revises: d14f0a3c8b22
"""

import sqlalchemy as sa
from alembic import op

revision = "e21a7c4b9f31"
down_revision = "d14f0a3c8b22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "character_relationships",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_unique_constraint(
        "uq_character_relationship_pair",
        "character_relationships",
        ["project_id", "source_character_id", "target_character_id"],
    )
    op.create_foreign_key(
        "fk_character_relationship_source",
        "character_relationships",
        "characters",
        ["source_character_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_character_relationship_target",
        "character_relationships",
        "characters",
        ["target_character_id"],
        ["id"],
    )
    op.alter_column("character_relationships", "version", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_character_relationship_target", "character_relationships", type_="foreignkey")
    op.drop_constraint("fk_character_relationship_source", "character_relationships", type_="foreignkey")
    op.drop_constraint("uq_character_relationship_pair", "character_relationships", type_="unique")
    op.drop_column("character_relationships", "version")
