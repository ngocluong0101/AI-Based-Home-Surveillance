"""VLM verification of behaviour events (V2-6)

A FALL or FIGHT can be checked by a vision-language model before anybody is bothered. `verification`:
  not_required  nothing to check (PERSON_DETECTED, RESTRICTED_AREA, or no VLM configured) - every event stored before this migration
  pending       waiting for the VLM (the Telegram message waits too, but never longer than VERIFY_TIMEOUT)
  confirmed     the VLM saw the event
  rejected      the VLM was sure it was not (no Telegram message; the event is still stored and shown, with the reason)
  unverified    the VLM was unsure, failed or was too slow: the alert goes out as if there were no VLM
`verification_score` is the VLM's probability of "yes"; `verification_reason` a sentence of what it saw; `verified_at` when it answered.

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
