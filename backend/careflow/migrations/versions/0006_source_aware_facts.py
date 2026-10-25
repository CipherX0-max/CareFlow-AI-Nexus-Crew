"""Add source-aware provenance and assertion columns to facts.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("facts", schema=None) as batch_op:
        batch_op.add_column(sa.Column("subject", sa.String(length=24), nullable=False, server_default="patient"))
        batch_op.add_column(sa.Column("relationship_to_patient", sa.String(length=32), nullable=False, server_default="patient"))
        batch_op.add_column(sa.Column("assertion_status", sa.String(length=32), nullable=False, server_default="present"))
        batch_op.add_column(sa.Column("section_heading", sa.String(length=160), nullable=True))
        batch_op.add_column(sa.Column("page_number", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("location_in_source", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("event_date", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("evidence_refs", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("facts", schema=None) as batch_op:
        batch_op.drop_column("evidence_refs")
        batch_op.drop_column("event_date")
        batch_op.drop_column("location_in_source")
        batch_op.drop_column("page_number")
        batch_op.drop_column("section_heading")
        batch_op.drop_column("assertion_status")
        batch_op.drop_column("relationship_to_patient")
        batch_op.drop_column("subject")
