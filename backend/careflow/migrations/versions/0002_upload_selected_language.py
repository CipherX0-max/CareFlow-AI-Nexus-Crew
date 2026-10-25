"""Record the language requested for audio transcription.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("selected_language", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("uploads", "selected_language")
