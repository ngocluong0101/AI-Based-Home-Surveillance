"""conf_threshold defaults to 0.25, what the detector produces anyway

The column existed since 0001 with a default of 0.5, but nothing used it: the engine filtered at the
0.25 of its nvinfer config for every camera. Now that the engine applies the value per camera, 0.5
would make every existing camera much stricter than it has ever been, so the default (and the rows
that still hold it, which nobody could have chosen: the form had no such field) becomes 0.25.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cameras", "conf_threshold", server_default=sa.text("0.25"))
    op.execute("UPDATE cameras SET conf_threshold = 0.25 WHERE conf_threshold = 0.5")


def downgrade() -> None:
    op.execute("UPDATE cameras SET conf_threshold = 0.5 WHERE conf_threshold = 0.25")
    op.alter_column("cameras", "conf_threshold", server_default=sa.text("0.5"))
