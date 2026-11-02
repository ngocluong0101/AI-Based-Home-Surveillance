"""cameras and events

Revision ID: 0001
Revises:
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cameras",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("source_type", sa.String(8), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("ai_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("desired_state", sa.String(8), server_default=sa.text("'stopped'"), nullable=False),
        sa.Column("conf_threshold", sa.Float(), server_default=sa.text("0.5"), nullable=False),
        sa.Column("absence_timeout", sa.Float(), server_default=sa.text("10"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("source_type IN ('file', 'rtsp')", name="ck_cameras_source_type"),
        sa.CheckConstraint("desired_state IN ('running', 'stopped')", name="ck_cameras_desired_state"),
        sa.CheckConstraint("conf_threshold >= 0 AND conf_threshold <= 1", name="ck_cameras_conf_threshold"),
        sa.CheckConstraint("absence_timeout >= 0", name="ck_cameras_absence_timeout"),
    )
    op.create_index(
        "uq_cameras_name_active", "cameras", ["name"], unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("camera_id", sa.Integer(), sa.ForeignKey("cameras.id"), nullable=False),
        sa.Column("pipeline_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("track_id", sa.BigInteger(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bbox", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_file", sa.String(192), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'new'"), nullable=False),
        sa.Column("notify_status", sa.String(16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_events_confidence"),
        sa.CheckConstraint("status IN ('new', 'acknowledged')", name="ck_events_status"),
        sa.CheckConstraint(
            "notify_status IN ('pending', 'sent', 'failed', 'disabled')", name="ck_events_notify_status"
        ),
    )
    op.create_index("ix_events_camera_timestamp", "events", ["camera_id", sa.text("timestamp DESC")])
    op.create_index("ix_events_timestamp", "events", [sa.text("timestamp DESC")])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("cameras")
