"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

how sure the skeleton network has to be, and for how long, before it calls a fall (per camera)

`cameras.fall_net_thr`: a score at or above it counts (0-1, default 0.9). `cameras.fall_net_k`: this many scores in a row make a fall
(1-100, default 3). Both only matter when `fall_net` is 1 or 2.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

THR_CK = "ck_cameras_fall_net_thr"
K_CK = "ck_cameras_fall_net_k"


def upgrade() -> None:
    op.add_column("cameras", sa.Column("fall_net_thr", sa.Float(), server_default=sa.text("0.9"), nullable=False))
    op.add_column("cameras", sa.Column("fall_net_k", sa.SmallInteger(), server_default=sa.text("3"), nullable=False))
    op.create_check_constraint(THR_CK, "cameras", "fall_net_thr >= 0 AND fall_net_thr <= 1")
    op.create_check_constraint(K_CK, "cameras", "fall_net_k >= 1 AND fall_net_k <= 100")


def downgrade() -> None:
    op.drop_constraint(K_CK, "cameras", type_="check")
    op.drop_constraint(THR_CK, "cameras", type_="check")
    op.drop_column("cameras", "fall_net_k")
    op.drop_column("cameras", "fall_net_thr")
