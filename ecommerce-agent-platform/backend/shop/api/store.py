"""Storefront API: sign-in, catalogue, cart, checkout, orders, support, policies.

These endpoints are normal application logic for a signed-in customer and enforce ownership in
their own handlers. SENTINEL governs the shopping agent's tool calls, not these UI requests.
"""
from __future__ import annotations

from shop.db import CommitBeforeResponseRoute

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from shop.db import get_db, utcnow
from shop.models import AuthSession, Inventory
from shop.security import Principal, current, customer, login
from shop.services import store
from shop.services.store import InventoryService, ServiceError

router = APIRouter(route_class=CommitBeforeResponseRoute)

_STATUS = {"not_found": 404, "invalid": 422, "conflict": 409, "not_eligible": 409, "unavailable": 503, "timeout": 504,
           "upstream_error": 502}


def fail(exc: ServiceError) -> HTTPException:
    return HTTPException(status_code=_STATUS.get(exc.classification, 400), detail=exc.message)


# ------------------------------------------------------------------ auth
class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=256)


@router.post("/auth/login")
def do_login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    token, user = login(db, body.email.strip(), body.password, request.client.host if request.client else "unknown")
    return {"token": token, "user": {"email": user.email, "role": user.role, "customer_id": user.customer_id,
                                     "display_name": user.display_name}}


@router.post("/auth/logout")
def do_logout(p: Principal = Depends(current), db: Session = Depends(get_db)):
    s = db.get(AuthSession, p.session_id)
    if s:
        s.revoked_at = utcnow()
    return {"ok": True}


@router.get("/auth/me")
def me(p: Principal = Depends(current)):
    return {"email": p.email, "role": p.role, "customer_id": p.customer_id, "display_name": p.display_name}


# ------------------------------------------------------------------ catalogue (public)
@router.get("/catalog/categories")
def cats(db: Session = Depends(get_db)):
    return {"items": store.categories(db)}


@router.get("/catalog/products")
def products(q: Optional[str] = Query(None, max_length=100), category: Optional[str] = Query(None, max_length=40),
             min_price: Optional[float] = Query(None, ge=0), max_price: Optional[float] = Query(None, ge=0),
             in_stock: bool = False, sort: Literal["relevance", "price_asc", "price_desc"] = "relevance",
             db: Session = Depends(get_db)):
    return {"items": store.search_products(db, q, category, min_price, max_price, in_stock, sort, 50, include_stock=True),
            "data_label": "Synthetic catalogue"}


@router.get("/catalog/products/{product_id}")
def product(product_id: str, db: Session = Depends(get_db)):
    try:
        p = store.get_product(db, product_id[:16])
    except ServiceError as exc:
        raise fail(exc)
    inv = db.get(Inventory, p.id)
    return store.product_dict(p, inv.available if inv else 0, full=True)


@router.get("/catalog/compare")
def compare(ids: str = Query(..., max_length=80), db: Session = Depends(get_db)):
    wanted = [x.strip() for x in ids.split(",") if x.strip()][:4]
    if len(wanted) < 2:
        raise HTTPException(status_code=422, detail="Choose two to four products")
    stock = InventoryService(db).available_map(wanted)
    try:
        return {"items": [store.product_dict(store.get_product(db, i), stock.get(i)) for i in wanted]}
    except ServiceError as exc:
        raise fail(exc)


@router.get("/policies")
def policies(db: Session = Depends(get_db)):
    return {"items": store.list_policies(db)}


@router.get("/policies/{topic}")
def policy(topic: str, db: Session = Depends(get_db)):
    try:
        p = store.get_policy(db, topic[:32])
    except ServiceError as exc:
        raise fail(exc)
    return {"topic": p.topic, "title": p.title, "body": p.body, "synthetic": p.synthetic}


# ------------------------------------------------------------------ cart & checkout (customer)
class AddIn(BaseModel):
    product_id: str = Field(pattern=r"^P-\d{4}$")
    quantity: int = Field(default=1, ge=1, le=10)


class QtyIn(BaseModel):
    quantity: int = Field(ge=1, le=10)


@router.get("/cart")
def get_cart(p: Principal = Depends(customer), db: Session = Depends(get_db)):
    return store.cart_view(db, p.customer_id)


@router.post("/cart/items", status_code=201)
async def add_item(body: AddIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    try:
        await store.add_to_cart(db, p.customer_id, body.product_id, body.quantity, InventoryService(db))
    except ServiceError as exc:
        raise fail(exc)
    return store.cart_view(db, p.customer_id)


@router.patch("/cart/items/{item_id}")
def set_qty(item_id: str, body: QtyIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    try:
        store.set_quantity(db, p.customer_id, item_id[:16], body.quantity)
    except ServiceError as exc:
        raise fail(exc)
    return store.cart_view(db, p.customer_id)


@router.delete("/cart/items/{item_id}")
def remove_item(item_id: str, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    if store.cart_item_owner(db, item_id[:16]) != p.customer_id:
        raise HTTPException(status_code=404, detail="Cart item not found")
    store.remove_cart_item(db, item_id[:16])
    return store.cart_view(db, p.customer_id)


class CheckoutIn(BaseModel):
    shipping_address: str = Field(min_length=10, max_length=300)
    payment_method: Literal["local_test"] = "local_test"


@router.post("/checkout", status_code=201)
async def do_checkout(body: CheckoutIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    try:
        return await store.checkout(db, p.customer_id, body.shipping_address, body.payment_method)
    except ServiceError as exc:
        raise fail(exc)


@router.get("/account/profile")
def profile(p: Principal = Depends(customer), db: Session = Depends(get_db)):
    return store.customer_profile(db, p.customer_id)


# ------------------------------------------------------------------ orders (customer)
def _own_order(db: Session, p: Principal, order_id: str):
    try:
        o = store.get_order(db, order_id[:16])
    except ServiceError as exc:
        raise fail(exc)
    if o.customer_id != p.customer_id:
        raise HTTPException(status_code=404, detail="Order not found")  # uniform: no existence leak
    return o


@router.get("/orders")
def orders(p: Principal = Depends(customer), db: Session = Depends(get_db)):
    return {"items": store.list_orders(db, p.customer_id)}


@router.get("/orders/{order_id}")
def order(order_id: str, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    o = _own_order(db, p, order_id)
    return {**store.order_details(db, o), "cancellation": store.cancellation_eligibility(db, o),
            "return": store.return_eligibility(db, o)}


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


@router.post("/orders/{order_id}/cancellation-request", status_code=201)
def cancel(order_id: str, body: ReasonIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    try:
        return store.request_cancellation(db, _own_order(db, p, order_id), body.reason, "storefront")
    except ServiceError as exc:
        raise fail(exc)


@router.post("/orders/{order_id}/return-request", status_code=201)
def ret(order_id: str, body: ReasonIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    try:
        return store.request_return(db, _own_order(db, p, order_id), body.reason, "storefront")
    except ServiceError as exc:
        raise fail(exc)


# ------------------------------------------------------------------ support (customer)
class SupportIn(BaseModel):
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=3, max_length=4000)
    order_id: Optional[str] = Field(default=None, pattern=r"^ORD-\d{4}$")


@router.get("/support")
def support(p: Principal = Depends(customer), db: Session = Depends(get_db)):
    return {"items": store.list_support(db, p.customer_id)}


@router.post("/support", status_code=201)
def new_support(body: SupportIn, p: Principal = Depends(customer), db: Session = Depends(get_db)):
    return store.create_support_request(db, p.customer_id, body.subject, body.message, body.order_id, "storefront")

