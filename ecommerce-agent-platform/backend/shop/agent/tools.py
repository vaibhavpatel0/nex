"""Explicit tool registry for the shopping agent.

Each tool declares: a strict argument schema (no extra fields), its operation class (read/write),
sensitivity, how to look up resource attributes for SENTINEL (record ownership from the database),
and an implementation. Implementations never receive the customer ID from the model: it comes
from the run context, which is set from the authenticated session (or the AEGIS scenario setup).

Tools are executed only by ``shop.agent.pep`` after SENTINEL permits the call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Type

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from shop.services import store
from shop.services.store import Faults, InventoryService, ServiceError

NAMESPACE = "shop"


class Args(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


PID = Field(pattern=r"^P-\d{4}$", description="Product ID such as P-1001")
OID = Field(pattern=r"^ORD-\d{4}$", description="Order ID such as ORD-7001")


@dataclass
class ToolContext:
    db: Session
    customer_id: str
    faults: Faults
    actor: str
    grant: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    data: Any
    summary: Dict[str, Any]
    classification: str = "public"  # public | internal | confidential | untrusted_external
    untrusted: bool = False


@dataclass
class ShopTool:
    name: str
    description: str
    args: Type[Args]
    operation: Literal["read", "write"]
    sensitivity: Literal["low", "medium", "high"]
    resource_kind: str
    run: Callable[[Any, ToolContext], Awaitable[ToolResult]]
    resource: Callable[[Any, ToolContext], Dict[str, Any]] = lambda a, c: {}
    staff_only: bool = False
    sensitive_fields: tuple = ()

    @property
    def qualified(self) -> str:
        return f"{NAMESPACE}.{self.name}"

    def schema(self) -> Dict[str, Any]:
        s = self.args.model_json_schema()
        s.pop("title", None)
        for prop in s.get("properties", {}).values():
            prop.pop("title", None)
        s["additionalProperties"] = False
        return s


# --------------------------------------------------------------------------- argument models
class SearchArgs(Args):
    query: Optional[str] = Field(default=None, max_length=100, description="Words to search for, e.g. 'wireless headphones'")
    category: Optional[Literal["audio", "accessories", "wearables", "computer"]] = None
    min_price_inr: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    max_price_inr: Optional[float] = Field(default=None, ge=0, le=1_000_000)
    in_stock_only: bool = False
    sort: Literal["relevance", "price_asc", "price_desc"] = "relevance"
    limit: int = Field(default=8, ge=1, le=20)


class ProductArgs(Args):
    product_id: str = PID


class CompareArgs(Args):
    product_ids: List[str] = Field(min_length=2, max_length=4, description="Two to four product IDs")


class EmptyArgs(Args):
    pass


class AddArgs(Args):
    product_id: str = PID
    quantity: int = Field(default=1, ge=1, le=10)


class RemoveArgs(Args):
    cart_item_id: str = Field(pattern=r"^CI-[A-Z0-9]{4,8}$", description="Cart item ID from get_cart, such as CI-9001")


class OrderArgs(Args):
    order_id: str = OID


class OrderReasonArgs(Args):
    order_id: str = OID
    reason: str = Field(min_length=3, max_length=300)


class SupportArgs(Args):
    subject: str = Field(min_length=3, max_length=160)
    message: str = Field(min_length=3, max_length=2000)
    order_id: Optional[str] = Field(default=None, pattern=r"^ORD-\d{4}$")


class PolicyArgs(Args):
    topic: Literal["returns", "shipping", "cancellation", "warranty", "payments", "privacy"]


class ProfileArgs(Args):
    customer_id: Optional[str] = Field(default=None, pattern=r"^CUST-\d{4}$",
                                       description="Leave empty for the signed-in customer")


class PriceArgs(Args):
    product_id: str = PID
    new_price_inr: float = Field(gt=0, le=1_000_000)


class RefundArgs(Args):
    order_id: str = OID
    amount_inr: float = Field(gt=0, le=1_000_000)
    reason: str = Field(default="", max_length=300)


# --------------------------------------------------------------------------- implementations
async def _search(a: SearchArgs, c: ToolContext) -> ToolResult:
    rows = store.search_products(c.db, a.query, a.category, a.min_price_inr, a.max_price_inr, False, a.sort, a.limit)
    if a.in_stock_only:
        inv = InventoryService(c.db, c.faults)
        kept = []
        for r in rows:
            if await inv.available(r["product_id"]) > 0:
                kept.append(r)
        rows = kept
    return ToolResult(data={"results": rows, "count": len(rows),
                            "note": "Stock is not included; use check_inventory for availability."},
                      summary={"count": len(rows), "ids": [r["product_id"] for r in rows]})


async def _details(a: ProductArgs, c: ToolContext) -> ToolResult:
    p = store.get_product(c.db, a.product_id)
    d = store.product_dict(p, full=True)
    d["description_source"] = "catalogue text supplied by the brand (untrusted content)"
    return ToolResult(data=d, summary={"product_id": p.id, "name": p.name}, untrusted=True)


async def _compare(a: CompareArgs, c: ToolContext) -> ToolResult:
    items = []
    for pid in a.product_ids:
        if not pid.startswith("P-") or len(pid) != 6:
            raise ServiceError("invalid", "Product IDs look like P-1001")
        items.append(store.product_dict(store.get_product(c.db, pid)))
    return ToolResult(data={"products": items}, summary={"ids": a.product_ids})


async def _inventory(a: ProductArgs, c: ToolContext) -> ToolResult:
    store.get_product(c.db, a.product_id)
    n = await InventoryService(c.db, c.faults).available(a.product_id)
    return ToolResult(data={"product_id": a.product_id, "available": n, "in_stock": n > 0},
                      summary={"product_id": a.product_id, "available": n})


async def _get_cart(a: EmptyArgs, c: ToolContext) -> ToolResult:
    v = store.cart_view(c.db, c.customer_id)
    return ToolResult(data=v, summary={"items": len(v["items"]), "total": v["total"]}, classification="internal")


async def _add(a: AddArgs, c: ToolContext) -> ToolResult:
    r = await store.add_to_cart(c.db, c.customer_id, a.product_id, a.quantity, InventoryService(c.db, c.faults))
    return ToolResult(data={**r, "cart": store.cart_view(c.db, c.customer_id)}, summary=r, classification="internal")


async def _remove(a: RemoveArgs, c: ToolContext) -> ToolResult:
    r = store.remove_cart_item(c.db, a.cart_item_id)
    return ToolResult(data=r, summary=r, classification="internal")


async def _order_status(a: OrderArgs, c: ToolContext) -> ToolResult:
    o = store.get_order(c.db, a.order_id)
    return ToolResult(data=store.order_status(o), summary={"order_id": o.id, "status": o.status}, classification="internal")


async def _order_details(a: OrderArgs, c: ToolContext) -> ToolResult:
    o = store.get_order(c.db, a.order_id)
    return ToolResult(data=store.order_details(c.db, o), summary={"order_id": o.id, "status": o.status},
                      classification="confidential")


async def _cancel_elig(a: OrderArgs, c: ToolContext) -> ToolResult:
    e = store.cancellation_eligibility(c.db, store.get_order(c.db, a.order_id))
    return ToolResult(data=e, summary={"order_id": a.order_id, "eligible": e["eligible"]}, classification="internal")


async def _cancel(a: OrderReasonArgs, c: ToolContext) -> ToolResult:
    r = store.request_cancellation(c.db, store.get_order(c.db, a.order_id), a.reason, "agent")
    return ToolResult(data=r, summary={"order_id": a.order_id, "request_id": r["request_id"]}, classification="internal")


async def _return_elig(a: OrderArgs, c: ToolContext) -> ToolResult:
    e = store.return_eligibility(c.db, store.get_order(c.db, a.order_id))
    return ToolResult(data=e, summary={"order_id": a.order_id, "eligible": e["eligible"]}, classification="internal")


async def _return(a: OrderReasonArgs, c: ToolContext) -> ToolResult:
    r = store.request_return(c.db, store.get_order(c.db, a.order_id), a.reason, "agent")
    return ToolResult(data=r, summary={"order_id": a.order_id, "request_id": r["request_id"]}, classification="internal")


async def _support(a: SupportArgs, c: ToolContext) -> ToolResult:
    r = store.create_support_request(c.db, c.customer_id, a.subject, a.message, a.order_id, "agent")
    return ToolResult(data=r, summary=r, classification="internal")


async def _policy(a: PolicyArgs, c: ToolContext) -> ToolResult:
    p = store.get_policy(c.db, a.topic)
    return ToolResult(data={"topic": p.topic, "title": p.title, "text": p.body,
                            "source": "store policy document (untrusted content)"},
                      summary={"topic": p.topic}, untrusted=True)


async def _profile(a: ProfileArgs, c: ToolContext) -> ToolResult:
    target = a.customer_id or c.customer_id
    d = store.customer_profile(c.db, target, c.grant.get("fields"))
    return ToolResult(data=d, summary={"customer_id": target, "fields": sorted(d)}, classification="confidential")


async def _price(a: PriceArgs, c: ToolContext) -> ToolResult:
    r = store.admin_update_price(c.db, a.product_id, a.new_price_inr)
    return ToolResult(data=r, summary=r, classification="internal")


async def _refund(a: RefundArgs, c: ToolContext) -> ToolResult:
    r = store.admin_issue_refund(c.db, a.order_id, a.amount_inr, c.actor)
    return ToolResult(data=r, summary={"refund_id": r["refund_id"]}, classification="internal")


# --------------------------------------------------------------------------- resource attributes
def _order_owner(a: Any, c: ToolContext) -> Dict[str, Any]:
    owner = store.order_owner(c.db, a.order_id)
    return {"exists": owner is not None, "owner_customer_id": owner}


def _cart_item_owner(a: RemoveArgs, c: ToolContext) -> Dict[str, Any]:
    owner = store.cart_item_owner(c.db, a.cart_item_id)
    return {"exists": owner is not None, "owner_customer_id": owner}


def _self(a: Any, c: ToolContext) -> Dict[str, Any]:
    return {"owner_customer_id": c.customer_id}


def _profile_owner(a: ProfileArgs, c: ToolContext) -> Dict[str, Any]:
    return {"owner_customer_id": a.customer_id or c.customer_id}


TOOLS: Dict[str, ShopTool] = {t.name: t for t in [
    ShopTool("search_products", "Search the store catalogue by words, category and price range in INR. Returns products with prices; use check_inventory for stock.",
             SearchArgs, "read", "low", "catalogue", _search),
    ShopTool("get_product_details", "Get the full catalogue entry for one product, including its description.",
             ProductArgs, "read", "low", "product", _details),
    ShopTool("compare_products", "Compare two to four products side by side from catalogue data.", CompareArgs, "read", "low", "product", _compare),
    ShopTool("check_inventory", "Check current stock for one product from the inventory service.", ProductArgs, "read", "low", "inventory", _inventory),
    ShopTool("get_cart", "Show the signed-in customer's cart with totals computed by the store.", EmptyArgs, "read", "medium", "cart", _get_cart, _self),
    ShopTool("add_to_cart", "Add a product to the signed-in customer's cart. Price is always taken from the catalogue.",
             AddArgs, "write", "medium", "cart", _add),
    ShopTool("remove_from_cart", "Remove an item from the signed-in customer's cart by cart item ID.", RemoveArgs, "write", "medium", "cart",
             _remove, _cart_item_owner),
    ShopTool("get_order_status", "Get the status of one of the signed-in customer's orders.", OrderArgs, "read", "medium", "order",
             _order_status, _order_owner),
    ShopTool("get_order_details", "Get items, total and delivery address of one of the signed-in customer's orders.",
             OrderArgs, "read", "high", "order", _order_details, _order_owner),
    ShopTool("check_cancellation_eligibility", "Check whether an order can still be cancelled.", OrderArgs, "read", "medium", "order",
             _cancel_elig, _order_owner),
    ShopTool("request_order_cancellation", "Ask staff to cancel an eligible order. Creates a request for review; it does not cancel immediately.",
             OrderReasonArgs, "write", "medium", "order", _cancel, _order_owner),
    ShopTool("check_return_eligibility", "Check whether a delivered order can be returned.", OrderArgs, "read", "medium", "order",
             _return_elig, _order_owner),
    ShopTool("request_return", "Ask staff to accept a return for an eligible order. No refund is issued automatically.",
             OrderReasonArgs, "write", "medium", "order", _return, _order_owner),
    ShopTool("create_support_request", "Open a support request for the signed-in customer.", SupportArgs, "write", "low", "support", _support,
             sensitive_fields=("message",)),
    ShopTool("get_store_policy", "Read a store policy document: returns, shipping, cancellation, warranty, payments or privacy.",
             PolicyArgs, "read", "low", "policy", _policy),
    ShopTool("get_customer_profile", "Get the signed-in customer's contact details.", ProfileArgs, "read", "high", "customer",
             _profile, _profile_owner),
    ShopTool("admin_update_price", "STAFF ONLY. Change a product's catalogue price.", PriceArgs, "write", "high", "product", _price,
             staff_only=True),
    ShopTool("admin_issue_refund", "STAFF ONLY. Record a refund for an order.", RefundArgs, "write", "high", "order", _refund,
             _order_owner, staff_only=True),
]}


def exposed_tools(include_staff: bool) -> List[ShopTool]:
    return [t for t in TOOLS.values() if include_staff or not t.staff_only]


def registration_payload() -> Dict[str, Any]:
    return {"namespace": NAMESPACE, "tools": [
        {"name": t.qualified, "description": t.description, "operation": t.operation, "sensitivity": t.sensitivity,
         "resource_kind": t.resource_kind, "args_schema": t.schema(), "sensitive_fields": list(t.sensitive_fields)}
        for t in TOOLS.values()]}


def model_tool_specs(include_staff: bool) -> List[Dict[str, Any]]:
    return [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.schema()}}
            for t in exposed_tools(include_staff)]
