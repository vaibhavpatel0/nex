"""Store data model. Money is stored as integer paise (1 INR = 100 paise).

All seeded customers, products and orders are SYNTHETIC test data (``synthetic`` column).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shop.db import Base, UTCDateTime, utcnow


class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=False)
    default_address: Mapped[str] = mapped_column(Text, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[Optional[str]] = mapped_column(ForeignKey("customers.id"))
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (
        CheckConstraint("role in ('customer','staff')", name="ck_user_role"),
        CheckConstraint("(role = 'customer') = (customer_id IS NOT NULL)", name="ck_user_customer_link"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)


class Product(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    brand: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    final_sale: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("price_paise > 0", name="ck_price_positive"),)


class Inventory(Base):
    __tablename__ = "inventory"
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), primary_key=True)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (CheckConstraint("available >= 0", name="ck_stock_nonnegative"),)


class Cart(Base):
    __tablename__ = "carts"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), unique=True, nullable=False)


class CartItem(Base):
    __tablename__ = "cart_items"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    cart_id: Mapped[str] = mapped_column(ForeignKey("carts.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    added_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("cart_id", "product_id", name="uq_cart_product"),
        CheckConstraint("quantity between 1 and 10", name="ck_cart_qty"),
    )


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    shipped_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_address: Mapped[str] = mapped_column(Text, nullable=False)
    payment_ref: Mapped[str] = mapped_column(String(40), nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    __table_args__ = (
        CheckConstraint("status in ('placed','processing','shipped','delivered','cancelled')", name="ck_order_status"),
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(160), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)


class CancellationRequest(Base):
    __tablename__ = "cancellation_requests"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending_review", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # storefront | agent
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ReturnRequest(Base):
    __tablename__ = "return_requests"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending_review", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class SupportRequest(Base):
    __tablename__ = "support_requests"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Refund(Base):
    """Refund records. Only staff tooling writes here; the shopping agent is never granted it.
    No money moves: there is no payment provider in this application."""

    __tablename__ = "refunds"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)
    amount_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending_review", nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class StorePolicy(Base):
    __tablename__ = "store_policies"
    topic: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# --------------------------------------------------------------------------- agent records
class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    started_by: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user | assistant | system_notice
    content: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class AgentRun(Base):
    """One agent execution: a customer message, an AEGIS scenario, or a gateway probe."""

    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)  # correlation id shared with SENTINEL
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # chat | test | probe
    conversation_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    customer_id: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    started_by: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    fixture: Mapped[str] = mapped_column(String(16), nullable=False)  # live | sandbox
    aegis_run_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    scenario_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    enforcement: Mapped[str] = mapped_column(String(16), default="enforced", nullable=False)  # enforced | baseline | unknown
    model_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    final_text: Mapped[Optional[str]] = mapped_column(Text)
    error_kind: Mapped[Optional[str]] = mapped_column(String(32))
    error: Mapped[Optional[str]] = mapped_column(Text)
    usage: Mapped[Optional[dict]] = mapped_column(JSON)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    blocked_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False, index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    __table_args__ = (
        CheckConstraint("kind in ('chat','test','probe')", name="ck_run_kind"),
        CheckConstraint("status in ('RUNNING','WAITING_FOR_MODEL','TOOL_REQUESTED','POLICY_CHECKING','BLOCKED',"
                        "'WAITING_FOR_APPROVAL','TOOL_EXECUTING','COMPLETED','FAILED')", name="ck_run_status"),
    )


class AgentEvent(Base):
    __tablename__ = "agent_events"
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(48), unique=True, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[Optional[str]] = mapped_column(String(24))
    tool: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    decision: Mapped[Optional[str]] = mapped_column(String(24), index=True)
    execution_status: Mapped[Optional[str]] = mapped_column(String(24))
    error_class: Mapped[Optional[str]] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    __table_args__ = (Index("ix_agent_events_corr_seq", "correlation_id", "seq"),)


class PendingAction(Base):
    """Arguments of an agent action held for approval, kept so staff can execute it once approved.
    Bound to the SENTINEL approval ID, which is itself bound to the exact argument hash."""

    __tablename__ = "pending_actions"
    approval_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    args: Mapped[dict] = mapped_column(JSON, nullable=False)
    customer_id: Mapped[str] = mapped_column(String(16), nullable=False)
    fixture: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)  # pending | executed | failed | discarded
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    executed_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime)
    executed_by: Mapped[Optional[str]] = mapped_column(String(200))
