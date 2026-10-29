"""Remote tools, service tokens, baseline runs

Adds what an external application (the e-commerce agent platform) needs to use SENTINEL as
its policy decision point:
- service_tokens: hashed credentials with scopes, bound to agent keys and tool namespaces
- tool_definitions.provider / revision / updated_at: tools registered by remote applications
- agents: 'remote_agent' type
- test_runs.mode: 'protected' (enforced) or 'baseline' (recorded, not enforced; sandbox only)
- tool_calls.enforced

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-29
"""
from alembic import op
import sqlalchemy as sa

import app.models.entities

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "service_tokens",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("agent_keys", sa.JSON(), nullable=False),
        sa.Column("tool_namespaces", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=True),
        sa.Column("created_at", app.models.entities.UTCDateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", app.models.entities.UTCDateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", app.models.entities.UTCDateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )
    with op.batch_alter_table("tool_definitions") as b:
        b.add_column(sa.Column("provider", sa.String(length=48), server_default="builtin", nullable=False))
        b.add_column(sa.Column("revision", sa.Integer(), server_default="1", nullable=False))
        b.add_column(sa.Column("updated_at", app.models.entities.UTCDateTime(timezone=True), nullable=True))
    with op.batch_alter_table("agents") as b:
        b.drop_constraint("ck_agent_type", type_="check")
        b.create_check_constraint(
            "ck_agent_type", "agent_type in ('simulated','local_model','hosted_model','remote_agent')"
        )
    with op.batch_alter_table("test_runs") as b:
        b.add_column(sa.Column("mode", sa.String(length=16), server_default="protected", nullable=False))
        b.create_check_constraint("ck_run_mode", "mode in ('protected','baseline')")
    with op.batch_alter_table("tool_calls") as b:
        b.add_column(sa.Column("enforced", sa.Boolean(), server_default="1", nullable=False))


def downgrade() -> None:
    with op.batch_alter_table("tool_calls") as b:
        b.drop_column("enforced")
    with op.batch_alter_table("test_runs") as b:
        b.drop_constraint("ck_run_mode", type_="check")
        b.drop_column("mode")
    with op.batch_alter_table("agents") as b:
        b.drop_constraint("ck_agent_type", type_="check")
        b.create_check_constraint("ck_agent_type", "agent_type in ('simulated','local_model','hosted_model')")
    with op.batch_alter_table("tool_definitions") as b:
        b.drop_column("updated_at")
        b.drop_column("revision")
        b.drop_column("provider")
    op.drop_table("service_tokens")
