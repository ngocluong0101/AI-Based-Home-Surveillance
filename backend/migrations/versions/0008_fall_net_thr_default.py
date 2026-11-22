"""the skeleton network's default alert threshold: 0.9 -> 0.95

The network in use is now the one trained on UR Fall + GMDCSA-24 (ROADMAP_V2_NHAT_KY.md 14.6). At 0.95 it caught 93% of the falls of UR Fall with 2.5% false
alarms (cross-validation) and 68% of the falls of an actor it had not seen with 4% false alarms; at 0.9 the false alarms on UR Fall were 7.5%. A camera that still
has exactly the old default (0.9) moves to the new one; any other value, chosen by someone, is kept. The downgrade moves the exact new default back.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-22
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("cameras", "fall_net_thr", server_default=sa.text("0.95"))
    op.execute("UPDATE cameras SET fall_net_thr = 0.95 WHERE fall_net_thr = 0.9")


def downgrade() -> None:
    op.alter_column("cameras", "fall_net_thr", server_default=sa.text("0.9"))
    op.execute("UPDATE cameras SET fall_net_thr = 0.9 WHERE fall_net_thr = 0.95")
