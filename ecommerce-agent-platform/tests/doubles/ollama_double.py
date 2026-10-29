"""TEST DOUBLE. A scripted server that speaks Ollama's /api/chat protocol, used ONLY by automated tests.

It is not a language model and is never configured outside tests. It lets the test suite drive the real
agent loop, the real enforcement point, SENTINEL and AEGIS deterministically. It deliberately follows
instructions embedded in tool output (like a vulnerable model) so tests can prove SENTINEL blocks them.

Run: uvicorn doubles.ollama_double:app --port N   (from the tests directory)
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI

app = FastAPI(title="Ollama protocol test double")
MODEL = "test-double:latest"


def call(name: str, **args: Any) -> Dict[str, Any]:
    return {"function": {"name": name, "arguments": args}}


def reply(content: str = "", calls: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"model": MODEL, "created_at": "2026-10-29T00:00:00Z", "done": True, "done_reason": "stop",
            "message": {"role": "assistant", "content": content, "tool_calls": calls or []},
            "prompt_eval_count": 120, "eval_count": 20, "total_duration": 1000000}


@app.get("/api/tags")
def tags():
    return {"models": [{"name": MODEL, "model": MODEL}]}


@app.post("/api/show")
def show(body: Dict[str, Any]):
    return {"capabilities": ["completion", "tools"], "details": {"family": "test-double"}}


def first_plan(user: str) -> Optional[List[Dict[str, Any]]]:
    u = user.lower()
    m = re.search(r"ORD-\d{4}", user)
    if "keep checking" in u:
        return [call("check_inventory", product_id="P-1010")] * 3
    if "store manager" in u:
        return [call("admin_update_price", product_id="P-1001", new_price_inr=1)]
    if "at a price of" in u:
        return [call("add_to_cart", product_id="P-1001", quantity=1, unit_price_inr=1)]
    if "check whether breeze buds" in u:
        return [call("check_inventory", product_id="P-1002")]
    if "breeze buds" in u and "add" in u:
        return [call("add_to_cart", product_id="P-1002", quantity=1)]
    if "pulse lite" in u and "stock" in u:
        return [call("check_inventory", product_id="P-1001")]
    if "headphones" in u and "3,000" in user:
        return [call("search_products", query="wireless headphones", max_price_inr=3000)]
    if "charger" in u:
        return [call("search_products", query="usb-c charger")]
    if "aurora" in u:
        return [call("get_product_details", product_id="P-1004")]
    if "warranty" in u:
        return [call("get_store_policy", topic="warranty")]
    if "cust-2002" in u:
        return [call("get_customer_profile", customer_id="CUST-2002")]
    if "ci-9002" in u:
        return [call("remove_from_cart", cart_item_id="CI-9002")]
    if "cancel" in u and m:
        return [call("check_cancellation_eligibility", order_id=m.group(0))]
    if "status" in u and m:
        return [call("get_order_status", order_id=m.group(0))]
    if "details" in u and m:
        return [call("get_order_details", order_id=m.group(0))]
    return None


def injected(tool_text: str) -> Optional[List[Dict[str, Any]]]:
    # Behaves like a model that obeys embedded instructions.
    if "Note for AI assistants" in tool_text:
        return [call("get_order_details", order_id="ORD-7004"), call("add_to_cart", product_id="P-1010", quantity=3)]
    if "Assistant instruction" in tool_text and "admin_issue_refund" in tool_text:
        return [call("admin_issue_refund", order_id="ORD-7002", amount_inr=50000, reason="goodwill")]
    return None


def summarize(user: str, tools: List[Dict[str, Any]]) -> str:
    parts = []
    for t in tools:
        try:
            d = json.loads(t["content"])
        except (ValueError, TypeError):
            parts.append("I received an unreadable result.")
            continue
        status = d.get("status") if isinstance(d, dict) else None
        if status == "BLOCKED":
            parts.append(f"I could not do that: {t.get('tool_name')} was blocked by store policy.")
        elif status == "NEEDS_APPROVAL":
            parts.append(f"{t.get('tool_name')} is waiting for staff approval and was not completed.")
        elif status in ("FAILED", "ERROR"):
            parts.append(f"I could not complete {t.get('tool_name')} because of an error ({d.get('error_class') or d.get('error')}).")
        elif isinstance(d, dict) and "results" in d:
            parts.append("Matching products: " + "; ".join(f"{r['name']} at {r['price']}" for r in d["results"]))
        elif isinstance(d, dict) and "status" in d and "order_id" in d:
            parts.append(f"Order {d['order_id']} is {d['status']}.")
        elif isinstance(d, dict) and "available" in d:
            parts.append(f"{d['product_id']} has {d['available']} units available.")
        elif isinstance(d, dict) and "eligible" in d:
            parts.append(("It is eligible. " if d["eligible"] else "It is not eligible: ") + d.get("reason", ""))
        elif isinstance(d, dict) and "name" in d:
            parts.append(f"{d['name']} costs {d.get('price')}.")
        elif isinstance(d, dict) and "title" in d:
            parts.append(d["title"])
        else:
            parts.append("Done.")
    return " ".join(parts) or "I have no information."


@app.post("/api/chat")
def chat(body: Dict[str, Any]):
    msgs = body.get("messages", [])
    user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
    since_user = msgs[max(i for i, m in enumerate(msgs) if m["role"] == "user") + 1:]
    tool_msgs = [m for m in since_user if m["role"] == "tool"]
    rounds = sum(1 for m in since_user if m["role"] == "assistant")
    if not tool_msgs:
        plan = first_plan(user)
        return reply(calls=plan) if plan else reply("I can help with products, carts, orders and policies.")
    last_round = tool_msgs[-1:] if rounds else tool_msgs
    if "keep checking" in user.lower() and rounds < 6:
        return reply(calls=[call("check_inventory", product_id="P-1010")] * 3)
    if rounds == 1:
        inj = injected(tool_msgs[-1].get("content", ""))
        if inj:
            return reply(calls=inj)
        if "check whether breeze buds" in user.lower():
            return reply(calls=[call("add_to_cart", product_id="P-1002", quantity=1)])
        if "cancel" in user.lower() and '"eligible": false' not in tool_msgs[-1]["content"]:
            m = re.search(r"ORD-\d{4}", user)
            return reply(calls=[call("request_order_cancellation", order_id=m.group(0), reason="Customer asked to cancel")])
    return reply(summarize(user, last_round if rounds > 1 else tool_msgs))
