"""Store business logic shared by the storefront API and the agent's tools.

Prices, totals, stock and eligibility are always computed here from the database. Nothing the
model says is trusted for these values.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from shop.db import as_utc, iso, utcnow
from shop.models import (
    CancellationRequest, Cart, CartItem, Customer, Inventory, Order, OrderItem, Product, Refund, ReturnRequest,
    StorePolicy, SupportRequest,
)

RETURN_WINDOW_DAYS = 30
MAX_QTY = 10
SHIPPING_FREE_FROM_PAISE = 99900
SHIPPING_FEE_PAISE = 7900


class ServiceError(Exception):
    """A classified business or service failure. ``classification`` is shown in traces."""

    def __init__(self, classification: str, message: str) -> None:
        super().__init__(message)
        self.classification = classification
        self.message = message


def inr(paise: int) -> str:
    rupees = paise / 100
    s = f"{rupees:,.2f}"
    # Indian digit grouping (12,34,567.00)
    whole, frac = s.replace(",", "").split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    return f"₹{whole}" + ("" if frac == "00" else f".{frac}")


def short_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"


# --------------------------------------------------------------------------- inventory service
@dataclass
class Faults:
    """Injected faults for sandbox test runs. Always empty for live store traffic."""

    values: Dict[str, str] = field(default_factory=dict)
    state: Dict[str, int] = field(default_factory=dict)

    def get(self, key: str) -> Optional[str]:
        return self.values.get(key)


class InventoryService:
    """Stands in for a warehouse inventory API. Faults simulate its failure modes in sandboxes only."""

    def __init__(self, db: Session, faults: Optional[Faults] = None) -> None:
        self.db = db
        self.faults = faults or Faults()

    async def _maybe_fail(self) -> None:
        mode = self.faults.get("inventory")
        if not mode or mode.startswith("race:"):
            return
        if mode == "unavailable":
            raise ServiceError("unavailable", "Inventory service refused the connection")
        if mode == "server_error":
            raise ServiceError("upstream_error", "Inventory service returned HTTP 500")
        if mode == "timeout":
            await asyncio.sleep(30)  # the caller's timeout fires first
            raise ServiceError("timeout", "Inventory service did not respond")

    async def available(self, product_id: str) -> int:
        await self._maybe_fail()
        row = self.db.get(Inventory, product_id)
        if row is None:
            raise ServiceError("not_found", "Unknown product")
        qty = int(row.available)
        mode = self.faults.get("inventory") or ""
        if mode == f"race:{product_id}" and not self.faults.state.get("race_done"):
            # Simulated concurrent purchase: stock disappears right after this read.
            row.available = 0
            row.updated_at = utcnow()
            self.db.flush()
            self.faults.state["race_done"] = 1
        return qty

    def available_map(self, ids: List[str]) -> Dict[str, int]:
        if not ids:
            return {}
        return {r.product_id: int(r.available) for r in self.db.execute(select(Inventory).where(Inventory.product_id.in_(ids))).scalars()}


# --------------------------------------------------------------------------- catalogue
def product_dict(p: Product, stock: Optional[int] = None, full: bool = False) -> Dict[str, Any]:
    d = {"product_id": p.id, "name": p.name, "brand": p.brand, "category": p.category, "price_inr": p.price_paise / 100,
         "price": inr(p.price_paise), "summary": p.summary, "final_sale": p.final_sale, "attributes": p.attributes,
         "synthetic": p.synthetic}
    if full:
        d["description"] = p.description
    if stock is not None:
        d["available"] = stock
        d["in_stock"] = stock > 0
    return d


def search_products(db: Session, query: Optional[str] = None, category: Optional[str] = None,
                    min_price_inr: Optional[float] = None, max_price_inr: Optional[float] = None,
                    in_stock_only: bool = False, sort: str = "relevance", limit: int = 10,
                    include_stock: bool = False) -> List[Dict[str, Any]]:
    stmt = select(Product).where(Product.active.is_(True))
    if category:
        stmt = stmt.where(Product.category == category.lower())
    if min_price_inr is not None:
        stmt = stmt.where(Product.price_paise >= int(round(min_price_inr * 100)))
    if max_price_inr is not None:
        stmt = stmt.where(Product.price_paise <= int(round(max_price_inr * 100)))
    rows = list(db.execute(stmt).scalars())
    if query:
        terms = [t for t in query.lower().replace(",", " ").split() if len(t) > 1]
        synonyms = {"headphones": ["headphones", "earbuds", "earphones", "neckband"], "headphone": ["headphones", "earbuds", "earphones"],
                    "earbuds": ["earbuds", "earphones"], "charger": ["charger"], "watch": ["smartwatch"]}

        def score(p: Product) -> int:
            hay = f"{p.name} {p.brand} {p.category} {p.summary} {' '.join(map(str, p.attributes.values()))}".lower()
            sc = 0
            for t in terms:
                alts = synonyms.get(t, [t[:-1] if t.endswith("s") and len(t) > 3 else t])
                if any(a in hay for a in alts):
                    sc += 1
            return sc

        scored = [(score(p), p) for p in rows]
        best = max((s for s, _ in scored), default=0)
        rows = [p for s, p in scored if best and s == best] if best else []
    stock = InventoryService(db).available_map([p.id for p in rows])
    if in_stock_only:
        rows = [p for p in rows if stock.get(p.id, 0) > 0]
    if sort == "price_asc":
        rows.sort(key=lambda p: p.price_paise)
    elif sort == "price_desc":
        rows.sort(key=lambda p: -p.price_paise)
    else:
        rows.sort(key=lambda p: (p.category, p.price_paise))
    return [product_dict(p, stock.get(p.id) if include_stock else None) for p in rows[:limit]]


def get_product(db: Session, product_id: str) -> Product:
    p = db.get(Product, product_id)
    if p is None or not p.active:
        raise ServiceError("not_found", "Product not found")
    return p


def categories(db: Session) -> List[str]:
    return sorted({c for (c,) in db.execute(select(Product.category).where(Product.active.is_(True)))})


# --------------------------------------------------------------------------- carts
def cart_for(db: Session, customer_id: str) -> Cart:
    c = db.execute(select(Cart).where(Cart.customer_id == customer_id)).scalar_one_or_none()
    if c is None:
        c = Cart(id=f"CART-{customer_id[-4:]}-{uuid.uuid4().hex[:4].upper()}", customer_id=customer_id)
        db.add(c)
        db.flush()
    return c


def cart_view(db: Session, customer_id: str) -> Dict[str, Any]:
    c = cart_for(db, customer_id)
    items = db.execute(select(CartItem, Product).join(Product, Product.id == CartItem.product_id)
                       .where(CartItem.cart_id == c.id).order_by(CartItem.added_at)).all()
    lines = []
    subtotal = 0
    for it, p in items:
        line = it.unit_price_paise * it.quantity
        subtotal += line
        lines.append({"cart_item_id": it.id, "product_id": p.id, "name": p.name, "quantity": it.quantity,
                      "unit_price": inr(it.unit_price_paise), "unit_price_inr": it.unit_price_paise / 100,
                      "line_total": inr(line), "price_changed": it.unit_price_paise != p.price_paise})
    shipping = 0 if subtotal == 0 or subtotal >= SHIPPING_FREE_FROM_PAISE else SHIPPING_FEE_PAISE
    return {"cart_id": c.id, "items": lines, "subtotal": inr(subtotal), "shipping": inr(shipping),
            "total": inr(subtotal + shipping), "total_inr": (subtotal + shipping) / 100, "item_count": sum(i["quantity"] for i in lines)}


async def add_to_cart(db: Session, customer_id: str, product_id: str, quantity: int, inventory: InventoryService) -> Dict[str, Any]:
    if not 1 <= quantity <= MAX_QTY:
        raise ServiceError("invalid", f"Quantity must be between 1 and {MAX_QTY}")
    p = get_product(db, product_id)
    c = cart_for(db, customer_id)
    existing = db.execute(select(CartItem).where(CartItem.cart_id == c.id, CartItem.product_id == product_id)).scalar_one_or_none()
    want = quantity + (existing.quantity if existing else 0)
    if want > MAX_QTY:
        raise ServiceError("invalid", f"At most {MAX_QTY} of one item per cart")
    stock = await inventory.available(product_id)
    # Re-read after the inventory call: stock may have changed meanwhile (checked again at checkout too).
    current = db.get(Inventory, product_id)
    stock = min(stock, int(current.available)) if current else 0
    if stock < want:
        raise ServiceError("conflict", f"Only {stock} in stock" if stock else "Out of stock")
    if existing:
        existing.quantity = want
        existing.unit_price_paise = p.price_paise
        item = existing
    else:
        item = CartItem(id=short_id("CI"), cart_id=c.id, product_id=product_id, quantity=quantity,
                        unit_price_paise=p.price_paise, added_at=utcnow())
        db.add(item)
    db.flush()
    return {"cart_item_id": item.id, "product_id": product_id, "quantity": item.quantity, "unit_price": inr(item.unit_price_paise)}


def cart_item_owner(db: Session, cart_item_id: str) -> Optional[str]:
    row = db.execute(select(Cart.customer_id).join(CartItem, CartItem.cart_id == Cart.id).where(CartItem.id == cart_item_id)).first()
    return row[0] if row else None


def remove_cart_item(db: Session, cart_item_id: str) -> Dict[str, Any]:
    """Removes the item. Ownership is enforced by the caller (storefront handler or SENTINEL for the agent)."""
    it = db.get(CartItem, cart_item_id)
    if it is None:
        raise ServiceError("not_found", "Cart item not found")
    db.delete(it)
    db.flush()
    return {"removed": cart_item_id}


def set_quantity(db: Session, customer_id: str, cart_item_id: str, quantity: int) -> None:
    it = db.get(CartItem, cart_item_id)
    if it is None or cart_item_owner(db, cart_item_id) != customer_id:
        raise ServiceError("not_found", "Cart item not found")
    if not 1 <= quantity <= MAX_QTY:
        raise ServiceError("invalid", f"Quantity must be between 1 and {MAX_QTY}")
    inv = db.get(Inventory, it.product_id)
    if inv is None or inv.available < quantity:
        raise ServiceError("conflict", "Not enough stock")
    it.quantity = quantity
    db.flush()


# --------------------------------------------------------------------------- orders
def order_owner(db: Session, order_id: str) -> Optional[str]:
    o = db.get(Order, order_id)
    return o.customer_id if o else None


def get_order(db: Session, order_id: str) -> Order:
    o = db.get(Order, order_id)
    if o is None:
        raise ServiceError("not_found", "Order not found")
    return o


def order_status(o: Order) -> Dict[str, Any]:
    return {"order_id": o.id, "status": o.status, "placed_at": iso(o.placed_at), "shipped_at": iso(o.shipped_at),
            "delivered_at": iso(o.delivered_at)}


def order_details(db: Session, o: Order) -> Dict[str, Any]:
    items = db.execute(select(OrderItem).where(OrderItem.order_id == o.id)).scalars().all()
    return {**order_status(o), "total": inr(o.total_paise), "shipping_address": o.shipping_address,
            "items": [{"product_id": i.product_id, "name": i.product_name, "quantity": i.quantity,
                       "unit_price": inr(i.unit_price_paise)} for i in items], "synthetic": o.synthetic}


def list_orders(db: Session, customer_id: str) -> List[Dict[str, Any]]:
    rows = db.execute(select(Order).where(Order.customer_id == customer_id).order_by(Order.placed_at.desc())).scalars().all()
    return [{**order_status(o), "total": inr(o.total_paise)} for o in rows]


def cancellation_eligibility(db: Session, o: Order) -> Dict[str, Any]:
    pending = db.execute(select(CancellationRequest).where(CancellationRequest.order_id == o.id,
                                                           CancellationRequest.status == "pending_review")).first()
    if pending:
        return {"order_id": o.id, "eligible": False, "reason": "A cancellation request is already waiting for review"}
    if o.status in ("placed", "processing"):
        return {"order_id": o.id, "eligible": True, "reason": f"Order is {o.status} and has not shipped"}
    return {"order_id": o.id, "eligible": False,
            "reason": {"shipped": "Order has already shipped; request a return after delivery",
                       "delivered": "Order was delivered; request a return instead",
                       "cancelled": "Order is already cancelled"}.get(o.status, "Not eligible")}


def return_eligibility(db: Session, o: Order) -> Dict[str, Any]:
    if o.status != "delivered" or o.delivered_at is None:
        return {"order_id": o.id, "eligible": False, "reason": "Only delivered orders can be returned"}
    age = (utcnow() - as_utc(o.delivered_at)).days
    finals = [i.product_name for i, p in db.execute(select(OrderItem, Product).join(Product, Product.id == OrderItem.product_id)
                                                     .where(OrderItem.order_id == o.id)).all() if p.final_sale]
    if finals:
        return {"order_id": o.id, "eligible": False, "reason": "Final-sale items cannot be returned: " + ", ".join(finals)}
    if age > RETURN_WINDOW_DAYS:
        return {"order_id": o.id, "eligible": False, "reason": f"Delivered {age} days ago; the return window is {RETURN_WINDOW_DAYS} days"}
    pending = db.execute(select(ReturnRequest).where(ReturnRequest.order_id == o.id, ReturnRequest.status == "pending_review")).first()
    if pending:
        return {"order_id": o.id, "eligible": False, "reason": "A return request is already waiting for review"}
    return {"order_id": o.id, "eligible": True, "reason": f"Delivered {age} days ago, within the {RETURN_WINDOW_DAYS}-day window",
            "days_left": RETURN_WINDOW_DAYS - age}


def request_cancellation(db: Session, o: Order, reason: str, source: str) -> Dict[str, Any]:
    e = cancellation_eligibility(db, o)
    if not e["eligible"]:
        raise ServiceError("not_eligible", e["reason"])
    r = CancellationRequest(id=short_id("CXL"), order_id=o.id, customer_id=o.customer_id, reason=reason[:300], source=source)
    db.add(r)
    db.flush()
    return {"request_id": r.id, "order_id": o.id, "status": r.status,
            "note": "Staff review the request; the order is not cancelled until they approve it."}


def request_return(db: Session, o: Order, reason: str, source: str) -> Dict[str, Any]:
    e = return_eligibility(db, o)
    if not e["eligible"]:
        raise ServiceError("not_eligible", e["reason"])
    r = ReturnRequest(id=short_id("RET"), order_id=o.id, customer_id=o.customer_id, reason=reason[:300], source=source)
    db.add(r)
    db.flush()
    return {"request_id": r.id, "order_id": o.id, "status": r.status,
            "note": "No refund is issued automatically. Staff review the return first."}


# --------------------------------------------------------------------------- checkout
async def checkout(db: Session, customer_id: str, shipping_address: str, payment_method: str) -> Dict[str, Any]:
    """Create an order from the cart. Prices and stock are re-validated here, inside one transaction."""
    if payment_method != "local_test":
        raise ServiceError("invalid", "Only the local test payment fixture is available; no real payment is processed")
    view = cart_view(db, customer_id)
    if not view["items"]:
        raise ServiceError("invalid", "Your cart is empty")
    c = cart_for(db, customer_id)
    items = db.execute(select(CartItem, Product).join(Product, Product.id == CartItem.product_id).where(CartItem.cart_id == c.id)).all()
    subtotal = 0
    for it, p in items:
        inv = db.get(Inventory, p.id)
        if inv is None or inv.available < it.quantity:
            raise ServiceError("conflict", f"{p.name} no longer has enough stock (available: {inv.available if inv else 0})")
        if it.unit_price_paise != p.price_paise:
            raise ServiceError("conflict", f"The price of {p.name} changed to {inr(p.price_paise)}. Review your cart.")
        subtotal += p.price_paise * it.quantity
    shipping = 0 if subtotal >= SHIPPING_FREE_FROM_PAISE else SHIPPING_FEE_PAISE
    oid = f"ORD-{uuid.uuid4().int % 9000 + 1000}"
    while db.get(Order, oid) is not None:
        oid = f"ORD-{uuid.uuid4().int % 9000 + 1000}"
    o = Order(id=oid, customer_id=customer_id, status="processing", placed_at=utcnow(), total_paise=subtotal + shipping,
              shipping_address=shipping_address[:300], payment_ref=f"TESTPAY-{uuid.uuid4().hex[:8].upper()}", synthetic=True)
    db.add(o)
    db.flush()
    for it, p in items:
        db.add(OrderItem(order_id=oid, product_id=p.id, product_name=p.name, quantity=it.quantity, unit_price_paise=p.price_paise))
        inv = db.get(Inventory, p.id)
        inv.available -= it.quantity
        inv.updated_at = utcnow()
        db.delete(it)
    db.flush()
    return {"order_id": oid, "total": inr(o.total_paise), "status": o.status, "payment": "Local test payment fixture (no money moved)"}


# --------------------------------------------------------------------------- support, policies, profile, staff
def create_support_request(db: Session, customer_id: str, subject: str, message: str, order_id: Optional[str], source: str) -> Dict[str, Any]:
    if order_id and order_owner(db, order_id) != customer_id:
        order_id = None  # never attach another customer's order
    r = SupportRequest(id=short_id("SUP"), customer_id=customer_id, order_id=order_id, subject=subject[:160], message=message[:4000],
                       source=source)
    db.add(r)
    db.flush()
    return {"request_id": r.id, "status": r.status}


def list_support(db: Session, customer_id: str) -> List[Dict[str, Any]]:
    rows = db.execute(select(SupportRequest).where(SupportRequest.customer_id == customer_id).order_by(SupportRequest.created_at.desc())).scalars()
    return [{"request_id": r.id, "subject": r.subject, "message": r.message, "order_id": r.order_id, "status": r.status,
             "source": r.source, "created_at": iso(r.created_at)} for r in rows]


def get_policy(db: Session, topic: str) -> StorePolicy:
    p = db.get(StorePolicy, topic.lower())
    if p is None:
        raise ServiceError("not_found", "Unknown policy topic")
    return p


def list_policies(db: Session) -> List[Dict[str, str]]:
    return [{"topic": p.topic, "title": p.title} for p in db.execute(select(StorePolicy).order_by(StorePolicy.topic)).scalars()]


def customer_profile(db: Session, customer_id: str, fields: Optional[List[str]] = None) -> Dict[str, Any]:
    c = db.get(Customer, customer_id)
    if c is None:
        raise ServiceError("not_found", "Customer not found")
    d = {"customer_id": c.id, "name": c.name, "email": c.email, "phone": c.phone, "default_address": c.default_address}
    return {k: v for k, v in d.items() if not fields or k in fields}


def admin_update_price(db: Session, product_id: str, new_price_inr: float) -> Dict[str, Any]:
    p = get_product(db, product_id)
    p.price_paise = max(1, int(round(new_price_inr * 100)))
    p.updated_at = utcnow()
    db.flush()
    return {"product_id": p.id, "price": inr(p.price_paise)}


def admin_issue_refund(db: Session, order_id: str, amount_inr: float, created_by: str) -> Dict[str, Any]:
    o = get_order(db, order_id)
    r = Refund(id=short_id("RFD"), order_id=o.id, amount_paise=int(round(amount_inr * 100)), created_by=created_by)
    db.add(r)
    db.flush()
    return {"refund_id": r.id, "status": r.status, "note": "Recorded for review; no payment provider exists in this app."}


def recent_window(days: int):
    return utcnow() - timedelta(days=days)

