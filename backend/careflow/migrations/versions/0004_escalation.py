"""Add escalation tracking columns to cases.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from backend.careflow.models import UTCDateTime

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.add_column(sa.Column("escalation_level", sa.String(length=12), nullable=True))
        batch_op.add_column(sa.Column("escalated_at", UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("escalated_by", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("escalation_notification", sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column("review_requested", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_column("review_requested")
        batch_op.drop_column("escalation_notification")
        batch_op.drop_column("escalated_by")
        batch_op.drop_column("escalated_at")
        batch_op.drop_column("escalation_level")
