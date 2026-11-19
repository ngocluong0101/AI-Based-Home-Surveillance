"""behaviour events (falls, fights, restricted areas) and the per-camera rules

`events` learns what kind of event it is beyond PERSON_DETECTED: the people involved (a fight has two), the zone of a
restricted-area event and when the behaviour BEGAN (a fall is reported two seconds after the impact; `timestamp` is the report,
`occurred_at` the moment the person hit the floor). Older rows get track_ids = [] (the API then answers [track_id]) and no
zone or occurred_at. `cameras` gets which rules run (`behaviors`) and the zones (`zones`).

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TYPE_CK = "ck_events_type"
TYPES = "('PERSON_DETECTED', 'FALL', 'FIGHT', 'RESTRICTED_AREA')"


def upgrade() -> None:
    op.add_column("events", sa.Column("track_ids", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("events", sa.Column("zone_id", sa.Integer(), nullable=True))
    op.add_column("events", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(TYPE_CK, "events", f"type IN {TYPES}")
    op.create_index("ix_events_type_timestamp", "events", ["type", sa.text("timestamp DESC")])
    op.add_column("cameras", sa.Column("behaviors", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("cameras", sa.Column("zones", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))


def downgrade() -> None:
    # Behaviour events have no meaning without the new columns, and the older schema allows only PERSON_DETECTED.
    op.execute("DELETE FROM events WHERE type <> 'PERSON_DETECTED'")
    op.drop_column("cameras", "zones")
    op.drop_column("cameras", "behaviors")
    op.drop_index("ix_events_type_timestamp", table_name="events")
    op.drop_constraint(TYPE_CK, "events", type_="check")
    op.drop_column("events", "occurred_at")
    op.drop_column("events", "zone_id")
    op.drop_column("events", "track_ids")
