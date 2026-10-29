"""Load the synthetic store fixtures into a database (the live store or a test sandbox)."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from shop.db import utcnow
from shop.models import (
    Cart, CartItem, Customer, Inventory, Order, OrderItem, Product, StorePolicy, User,
)

SEED_DIR = Path(__file__).resolve().parent
FIXTURES = json.loads((SEED_DIR / "fixtures.json").read_text())


def _policy_doc(topic: str) -> Dict[str, str]:
    text = (SEED_DIR / "policies" / f"{topic}.md").read_text()
    title = text.splitlines()[0].lstrip("# ").strip()
    return {"topic": topic, "title": title, "body": text}


def seed_store(db: Session, password_hash: Optional[str] = None) -> bool:
    """Insert fixtures. Returns False when the store already has data (idempotent)."""
    if db.execute(select(Customer.id).limit(1)).first() is not None:
        return False
    now = utcnow()
    prices: Dict[str, int] = {}
    names: Dict[str, str] = {}
    # Explicit flushes keep inserts in foreign-key order (the models have no relationship() links).
    for c in FIXTURES["customers"]:
        db.add(Customer(synthetic=True, **c))
    db.flush()
    for p in FIXTURES["products"]:
        prices[p["id"]] = int(p["price_inr"] * 100)
        names[p["id"]] = p["name"]
        db.add(Product(id=p["id"], name=p["name"], brand=p["brand"], category=p["category"], price_paise=prices[p["id"]],
                       summary=p["summary"], description=p["description"], attributes=p["attributes"],
                       final_sale=bool(p.get("final_sale", False)), synthetic=True, updated_at=now))
    db.flush()
    for p in FIXTURES["products"]:
        db.add(Inventory(product_id=p["id"], available=int(p["stock"]), updated_at=now))
    db.flush()
    for cart in FIXTURES["carts"]:
        db.add(Cart(id=cart["id"], customer_id=cart["customer_id"]))
        db.flush()
        for it in cart["items"]:
            db.add(CartItem(id=it["id"], cart_id=cart["id"], product_id=it["product_id"], quantity=it["quantity"],
                            unit_price_paise=prices[it["product_id"]], added_at=now))
    addr = {c["id"]: c["default_address"] for c in FIXTURES["customers"]}
    for o in FIXTURES["orders"]:
        total = sum(prices[i["product_id"]] * i["quantity"] for i in o["items"])
        d = lambda k: now - timedelta(days=o[k]) if k in o else None  # noqa: E731
        db.add(Order(id=o["id"], customer_id=o["customer_id"], status=o["status"], placed_at=d("placed_days_ago"),
                     shipped_at=d("shipped_days_ago"), delivered_at=d("delivered_days_ago"), total_paise=total,
                     shipping_address=addr[o["customer_id"]], payment_ref=f"TESTPAY-{o['id'][-4:]}", synthetic=True))
        db.flush()
        for i in o["items"]:
            db.add(OrderItem(order_id=o["id"], product_id=i["product_id"], product_name=names[i["product_id"]],
                             quantity=i["quantity"], unit_price_paise=prices[i["product_id"]]))
    for topic in FIXTURES["policies"]:
        db.add(StorePolicy(synthetic=True, **_policy_doc(topic)))
    if password_hash is not None:
        from shop.security import new_id
        for u in FIXTURES["users"]:
            db.add(User(id=new_id("usr"), email=u["email"], role=u["role"], customer_id=u["customer_id"],
                        display_name=u["display_name"], password_hash=password_hash, created_at=now))
    db.flush()
    return True
