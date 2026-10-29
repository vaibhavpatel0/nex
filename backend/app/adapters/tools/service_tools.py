"""Simulated external services: an inventory API with fault injection, and a fixture-only web fetcher.

Fault modes (set per scenario in ``ToolContext.faults``):
  timeout            the upstream never answers within the gateway timeout
  rate_limited       HTTP 429 on every call
  rate_limited_then_ok:N   HTTP 429 for the first N calls, then success
  server_error       HTTP 500
  malformed          a 200 response with a body that is not valid JSON
  unavailable        connection refused
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
from typing import Any, Dict
from urllib.parse import urlsplit

from pydantic import Field

from app.adapters.tools.base import ProtectedTool, StrictArgs, ToolContext, ToolError, ToolOutput, ToolSpec
from app.config.settings import get_settings


def _apply_fault(ctx: ToolContext, service: str) -> "str | None":
    mode = ctx.faults.get(service)
    if not mode:
        return None
    count = ctx.fault_state.get(service, 0) + 1
    ctx.fault_state[service] = count
    if mode.startswith("rate_limited_then_ok:"):
        n = int(mode.split(":", 1)[1])
        return "rate_limited" if count <= n else None
    return mode


async def _raise_for_fault(mode: "str | None") -> "str | None":
    if mode is None:
        return None
    if mode == "timeout":
        await asyncio.sleep(get_settings().simulated_timeout_delay_seconds)
        raise ToolError("timeout", "Upstream did not respond")  # only reached if gateway timeout is longer
    if mode == "rate_limited":
        raise ToolError("upstream_rate_limited", "Upstream returned HTTP 429 Too Many Requests")
    if mode == "server_error":
        raise ToolError("upstream_error", "Upstream returned HTTP 500")
    if mode == "unavailable":
        raise ToolError("unavailable", "Connection refused by upstream service")
    if mode == "malformed":
        return "{stock: forty-two,,"
    return None


class StockArgs(StrictArgs):
    sku: str = Field(pattern=r"^SKU-\d{3}$")


class StockTool(ProtectedTool):
    spec = ToolSpec(
        name="inventory.get_stock",
        description="Query the (simulated) inventory API for available stock of a SKU.",
        operation="read",
        sensitivity="low",
        resource_kind="external_api",
        args_model=StockArgs,
    )

    async def _run(self, args: StockArgs, ctx: ToolContext) -> ToolOutput:
        raw = await _raise_for_fault(_apply_fault(ctx, "inventory"))
        if raw is None:
            stock = ctx.sandbox.read_json("inventory.json")
            if args.sku not in stock:
                raise ToolError("not_found", "Unknown SKU")
            raw = json.dumps({"sku": args.sku, "available": stock[args.sku], "warehouse": "west"})
        try:
            body = json.loads(raw)
            available = int(body["available"])
        except (ValueError, KeyError, TypeError):
            raise ToolError("invalid_response", "Upstream returned a response that is not valid JSON")
        return ToolOutput(
            data=body,
            text=f"{args.sku}: {available} units available.",
            classification="public",
            summary={"sku": args.sku, "available": available},
        )


class AdjustStockArgs(StrictArgs):
    sku: str = Field(pattern=r"^SKU-\d{3}$")
    delta: int = Field(ge=-1000, le=1000)


class AdjustStockTool(ProtectedTool):
    spec = ToolSpec(
        name="inventory.adjust_stock",
        description="Adjust stock for a SKU on the (simulated) inventory API.",
        operation="write",
        sensitivity="high",
        resource_kind="external_api",
        args_model=AdjustStockArgs,
    )

    async def _run(self, args: AdjustStockArgs, ctx: ToolContext) -> ToolOutput:
        await _raise_for_fault(_apply_fault(ctx, "inventory"))
        stock = ctx.sandbox.read_json("inventory.json")
        if args.sku not in stock:
            raise ToolError("not_found", "Unknown SKU")
        stock[args.sku] = max(0, stock[args.sku] + args.delta)
        ctx.sandbox.write_json("inventory.json", stock)
        return ToolOutput(
            data={"sku": args.sku, "available": stock[args.sku]},
            text=f"{args.sku} adjusted by {args.delta}; now {stock[args.sku]}.",
            classification="internal",
            summary={"sku": args.sku, "delta": args.delta},
        )


class FetchArgs(StrictArgs):
    url: str = Field(min_length=8, max_length=300)


def url_attributes(url: str) -> Dict[str, Any]:
    """Parse a URL for SSRF checks. Never resolves DNS and never connects."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return {"valid": False}
    is_ip = False
    private = False
    try:
        ip = ipaddress.ip_address(host)
        is_ip = True
        private = ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
    except ValueError:
        pass
    return {
        "valid": bool(parts.scheme and host),
        "scheme": parts.scheme.lower(),
        "host": host,
        "port": port,
        "has_credentials": bool(parts.username or parts.password),
        "is_ip_literal": is_ip,
        "private_address": private or host in ("localhost",) or host.endswith(".localhost") or host.endswith(".internal"),
    }


class FetchTool(ProtectedTool):
    spec = ToolSpec(
        name="web.fetch",
        description="Fetch a web page. In this prototype only bundled fixture pages are served; no network access occurs.",
        operation="read",
        sensitivity="medium",
        resource_kind="url",
        args_model=FetchArgs,
    )

    def resource_attributes(self, args: FetchArgs, ctx: ToolContext) -> Dict[str, Any]:
        return url_attributes(args.url)

    async def _run(self, args: FetchArgs, ctx: ToolContext) -> ToolOutput:
        pages = ctx.sandbox.read_json("web_pages.json")["pages"]
        page = pages.get(args.url)
        if page is None:
            raise ToolError("not_found", "Page not available in the fixture set")
        return ToolOutput(
            data=page,
            text=f"{page['title']}\n{page['body']}",
            classification="untrusted_external",
            untrusted=True,
            summary={"title": page["title"]},
        )
