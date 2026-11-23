"""
@author: Ngoc Luong <ngocluongg0101@gmail.com>

VLM verification of behaviour events (V2-6)

`verification`: not_required (nothing to check, or no VLM; every older event), pending (waiting for the VLM, Telegram waits at most
VERIFY_TIMEOUT), confirmed, rejected (the VLM was sure it was not: no Telegram message, still stored and shown), unverified (unsure,
failed or too slow: the alert goes out as without a VLM). `verification_score` = P(yes), `verification_reason` = a sentence of what it
saw, `verified_at` = when it answered.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

CK = "ck_events_verification"
VALUES = "('not_required', 'pending', 'confirmed', 'rejected', 'unverified')"


def upgrade() -> None:
    op.add_column("events", sa.Column("verification", sa.String(16), server_default=sa.text("'not_required'"), nullable=False))
    op.add_column("events", sa.Column("verification_score", sa.Float(), nullable=True))
    op.add_column("events", sa.Column("verification_reason", sa.Text(), nullable=True))
    op.add_column("events", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(CK, "events", f"verification IN {VALUES}")
    op.create_index("ix_events_verification", "events", ["verification"], postgresql_where=sa.text("verification = 'pending'"))


def downgrade() -> None:
    op.drop_index("ix_events_verification", table_name="events")
    op.drop_constraint(CK, "events", type_="check")
    op.drop_column("events", "verified_at")
    op.drop_column("events", "verification_reason")
    op.drop_column("events", "verification_score")
    op.drop_column("events", "verification")
