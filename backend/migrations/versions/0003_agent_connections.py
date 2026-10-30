"""Agent connections: URL discovery, ownership verification, scoped credentials, test scopes, audit

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30
"""
from alembic import op
import sqlalchemy as sa

import app.models.entities

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = app.models.entities.UTCDateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "agent_connections",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_user_id", sa.String(40), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("submitted_url", sa.String(2048), nullable=False),
        sa.Column("host", sa.String(255), nullable=False),
        sa.Column("protocol", sa.String(24), nullable=False),
        sa.Column("protocol_version", sa.String(32)),
        sa.Column("interaction_endpoint", sa.String(2048)),
        sa.Column("descriptor", sa.JSON(), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("discovered_at", TS),
        sa.Column("verification_status", sa.String(16), nullable=False, server_default="unverified"),
        sa.Column("verified_domain", sa.String(255)),
        sa.Column("verification_method", sa.String(24)),
        sa.Column("verified_at", TS),
        sa.Column("verified_by", sa.String(64)),
        sa.Column("auth_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("authorized_at", TS),
        sa.Column("authorized_by", sa.String(64)),
        sa.Column("granted_permissions", sa.JSON(), nullable=False),
        sa.Column("health_status", sa.String(16), nullable=False, server_default="unchecked"),
        sa.Column("health_detail", sa.Text()),
        sa.Column("last_health_at", TS),
        sa.Column("last_healthy_at", TS),
        sa.Column("mode", sa.String(16), nullable=False, server_default="none"),
        sa.Column("agent_id", sa.String(40), sa.ForeignKey("agents.id", ondelete="SET NULL")),
        sa.Column("namespace", sa.String(24), nullable=False, unique=True),
        sa.Column("service_token_id", sa.String(40), sa.ForeignKey("service_tokens.id", ondelete="SET NULL")),
        sa.Column("disconnected_at", TS),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.CheckConstraint("protocol in ('a2a','mcp','openapi','custom_http','none')", name="ck_conn_protocol"),
        sa.CheckConstraint("verification_status in ('unverified','pending','verified','expired','failed')", name="ck_conn_verif"),
        sa.CheckConstraint("auth_status in ('none','authorized','expired','revoked')", name="ck_conn_auth"),
        sa.CheckConstraint("mode in ('none','monitoring','protected')", name="ck_conn_mode"),
    )
    op.create_index("ix_agent_connections_owner_user_id", "agent_connections", ["owner_user_id"])
    op.create_index("ix_agent_connections_host", "agent_connections", ["host"])

    op.create_table(
        "connection_challenges",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("connection_id", sa.String(40), sa.ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("value", sa.String(128), nullable=False),
        sa.Column("location", sa.String(2048), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("detail", sa.Text()),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("checked_at", TS),
        sa.CheckConstraint("method in ('dns_txt','https_file')", name="ck_chal_method"),
        sa.CheckConstraint("status in ('pending','verified','failed','expired')", name="ck_chal_status"),
    )
    op.create_index("ix_connection_challenges_connection_id", "connection_challenges", ["connection_id"])

    op.create_table(
        "connection_credentials",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("connection_id", sa.String(40), sa.ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("header_name", sa.String(64)),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("meta", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("expires_at", TS),
        sa.Column("created_by", sa.String(64), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
        sa.CheckConstraint("kind in ('bearer','header','oauth2_client_credentials')", name="ck_cred_kind"),
    )
    op.create_index("ix_connection_credentials_connection_id", "connection_credentials", ["connection_id"])

    op.create_table(
        "connection_test_scopes",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("connection_id", sa.String(40), sa.ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("suites", sa.JSON(), nullable=False),
        sa.Column("max_runs_per_day", sa.Integer(), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=False),
        sa.Column("approved_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("revoked_at", TS),
    )
    op.create_index("ix_connection_test_scopes_connection_id", "connection_test_scopes", ["connection_id"])

    op.create_table(
        "connection_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("connection_id", sa.String(40), sa.ForeignKey("agent_connections.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="ok"),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_connection_events_connection_id", "connection_events", ["connection_id"])


def downgrade() -> None:
    for t in ("connection_events", "connection_test_scopes", "connection_credentials", "connection_challenges",
              "agent_connections"):
        op.drop_table(t)
