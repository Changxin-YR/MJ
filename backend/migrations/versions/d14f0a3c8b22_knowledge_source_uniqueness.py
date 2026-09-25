"""enforce one knowledge document per project source

Revision ID: d14f0a3c8b22
Revises: c91d4e6f2a10
"""

from alembic import op

revision = "d14f0a3c8b22"
down_revision = "c91d4e6f2a10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_knowledge_document_project_source",
        "knowledge_documents",
        ["project_id", "source_type", "source_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_document_project_source",
        "knowledge_documents",
        type_="unique",
    )
