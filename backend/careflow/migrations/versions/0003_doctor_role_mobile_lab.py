"""Add doctor role, age, mobile_e164 to patient_profiles; add lab_results to uploads.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # patient_profiles: add age and mobile_e164
    with op.batch_alter_table("patient_profiles", schema=None) as batch_op:
        batch_op.add_column(sa.Column("age", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mobile_e164", sa.String(length=20), nullable=True))

    # uploads: add lab_results JSON column
    with op.batch_alter_table("uploads", schema=None) as batch_op:
        batch_op.add_column(sa.Column("lab_results", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("uploads", schema=None) as batch_op:
        batch_op.drop_column("lab_results")

    with op.batch_alter_table("patient_profiles", schema=None) as batch_op:
        batch_op.drop_column("mobile_e164")
        batch_op.drop_column("age")
