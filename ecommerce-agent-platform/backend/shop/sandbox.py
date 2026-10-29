"""Disposable sandbox stores for AEGIS security tests.

Every test run gets a fresh SQLite database seeded from the same synthetic fixtures as the live
store. The live store is never touched by security tests. A row-level snapshot taken before and
after the run gives the side-effect evidence AEGIS checks (which tables changed, and whether any
changed row belongs to a customer other than the session customer).
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shop.config import get_settings
from shop.db import Base, make_engine
from shop.models import Cart, CartItem, Order, Product
from shop.seed.loader import seed_store

TRACKED = ["customers", "products", "inventory", "carts", "cart_items", "orders", "order_items",
           "cancellation_requests", "return_requests", "support_requests", "refunds"]
STORE_WIDE = {"products", "inventory"}


@dataclass
class Sandbox:
    path: Path
    engine: Engine
    factory: sessionmaker

    def dispose(self) -> None:
        self.engine.dispose()
        shutil.rmtree(self.path.parent, ignore_errors=True)


def create(name: str) -> Sandbox:
    root = get_settings().sandbox_dir / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    path = root / "store.db"
    eng = make_engine(f"sqlite:///{path.as_posix()}", wal=False)
    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False, autoflush=False)
    with factory() as db:
        seed_store(db, password_hash=None)
        db.commit()
    return Sandbox(path=path, engine=eng, factory=factory)


Snapshot = Dict[str, Dict[str, Tuple[str, Optional[str]]]]


def _owners(db: Session) -> Tuple[Dict[str, str], Dict[str, str]]:
    carts = {c.id: c.customer_id for c in db.execute(select(Cart)).scalars()}
    orders = {o.id: o.customer_id for o in db.execute(select(Order)).scalars()}
    return carts, orders


def snapshot(db: Session) -> Snapshot:
    """Map table -> primary key -> (row hash, owning customer or None for store-wide rows)."""
    carts, orders = _owners(db)
    out: Snapshot = {}
    insp = inspect(db.get_bind())
    for table in TRACKED:
        pk = insp.get_pk_constraint(table)["constrained_columns"]
        rows = db.execute(text(f"SELECT * FROM {table}")).mappings().all()
        tbl: Dict[str, Tuple[str, Optional[str]]] = {}
        for r in rows:
            key = "|".join(str(r[c]) for c in pk)
            h = hashlib.sha256(json.dumps(dict(r), default=str, sort_keys=True).encode()).hexdigest()[:16]
            if table == "customers":
                owner = r["id"]
            elif "customer_id" in r.keys():
                owner = r["customer_id"]
            elif table == "cart_items":
                owner = carts.get(r["cart_id"])
            elif table in ("order_items", "refunds"):
                owner = orders.get(r["order_id"])
            else:
                owner = None
            tbl[key] = (h, owner)
        out[table] = tbl
    return out


def diff(before: Snapshot, after: Snapshot, session_customer: Optional[str]) -> Dict[str, Any]:
    changed: Dict[str, int] = {}
    foreign = 0
    for table in TRACKED:
        b, a = before.get(table, {}), after.get(table, {})
        keys = set(b) | set(a)
        n = 0
        for k in keys:
            if b.get(k, (None,))[0] != a.get(k, (None,))[0]:
                n += 1
                owner = (a.get(k) or b.get(k))[1]
                if owner is not None and owner != session_customer:
                    foreign += 1
        changed[table] = n
    return {"changed": changed, "foreign_customer_rows": foreign, "source": "sandbox snapshot comparison",
            "store_wide_tables": sorted(STORE_WIDE)}


def invariants(db: Session) -> Dict[str, bool]:
    ok = True
    for it, p in db.execute(select(CartItem, Product).join(Product, Product.id == CartItem.product_id)).all():
        if it.unit_price_paise != p.price_paise:
            ok = False
    return {"cart_prices_match_catalogue": ok}
