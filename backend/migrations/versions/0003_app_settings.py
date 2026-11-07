"""app_settings, and 'skipped' as a notify_status

`app_settings` holds what is changed from the web app (alerts on or off, quiet hours, snapshot retention, what a new
camera starts with). `skipped` is a notify_status for an event whose alert was held back on purpose (alerts switched
off, or received during the quiet hours): the event is stored and shown, no Telegram message was sent.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

NAME = "ck_events_notify_status"


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.drop_constraint(NAME, "events", type_="check")
    op.create_check_constraint(NAME, "events", "notify_status IN ('pending', 'sent', 'failed', 'disabled', 'skipped')")


def downgrade() -> None:
    # The nearest older meaning of "no message was sent": Telegram not in use.
    op.execute("UPDATE events SET notify_status = 'disabled' WHERE notify_status = 'skipped'")
    op.drop_constraint(NAME, "events", type_="check")
    op.create_check_constraint(NAME, "events", "notify_status IN ('pending', 'sent', 'failed', 'disabled')")
    op.drop_table("app_settings")
