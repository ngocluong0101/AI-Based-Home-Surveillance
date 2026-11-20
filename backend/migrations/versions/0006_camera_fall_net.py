"""which detector decides a fall on a camera: the rules, the skeleton network (ST-GCN), or both

`cameras.fall_net`: 0 = the rules only (what every camera did until now), 1 = the network decides, 2 = the network or the rules.
It only matters on a camera with the "fall" rule; the engine falls back to the rules when it has no network model.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

CK = "ck_cameras_fall_net"


def upgrade() -> None:
    op.add_column("cameras", sa.Column("fall_net", sa.SmallInteger(), server_default=sa.text("0"), nullable=False))
    op.create_check_constraint(CK, "cameras", "fall_net IN (0, 1, 2)")


def downgrade() -> None:
    op.drop_constraint(CK, "cameras", type_="check")
    op.drop_column("cameras", "fall_net")
