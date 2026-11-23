"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

conf_threshold defaults to 0.25, what the detector produces anyway

The column had a default of 0.5 that nothing used (the engine filtered at 0.25 for every camera). Now the engine applies the value
per camera, so 0.5 would make every existing camera much stricter: the default, and the rows still holding it, become 0.25.

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
