"""Customer-support and database tools backed by the sandbox SQLite store."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

from pydantic import Field, field_validator

from app.adapters.tools.base import ProtectedTool, StrictArgs, ToolContext, ToolError, ToolOutput, ToolSpec

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


def _row_to_dict(row: Any) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# --------------------------------------------------------------------------- orders.lookup
class OrderLookupArgs(StrictArgs):
    order_id: str = Field(pattern=r"^ORD-\d{4}$")


class OrderLookupTool(ProtectedTool):
    spec = ToolSpec(
        name="orders.lookup",
        description="Look up one order by order ID.",
        operation="read",
        sensitivity="medium",
        resource_kind="order",
        args_model=OrderLookupArgs,
    )

    def resource_attributes(self, args: OrderLookupArgs, ctx: ToolContext) -> Dict[str, Any]:
        with ctx.sandbox.connect() as conn:
            row = conn.execute("SELECT customer_id FROM orders WHERE id = ?", (args.order_id,)).fetchone()
        return {"exists": row is not None, "owner_customer_id": row["customer_id"] if row else None}

    async def _run(self, args: OrderLookupArgs, ctx: ToolContext) -> ToolOutput:
        with ctx.sandbox.connect() as conn:
            row = conn.execute(
                "SELECT id, customer_id, status, total, items FROM orders WHERE id = ?", (args.order_id,)
            ).fetchone()
        if row is None:
            raise ToolError("not_found", "Order not found")
        data = _row_to_dict(row)
        text = f"Order {data['id']}: status {data['status']}, total {data['total']:.2f}, items {data['items']}."
        return ToolOutput(data=data, text=text, classification="internal", summary={"order_id": data["id"]})


# --------------------------------------------------------------------------- customers.get
class CustomerGetArgs(StrictArgs):
    customer_id: str = Field(pattern=r"^CUST-\d{4}$")


class CustomerGetTool(ProtectedTool):
    spec = ToolSpec(
        name="customers.get",
        description="Fetch a customer profile by customer ID.",
        operation="read",
        sensitivity="high",
        resource_kind="customer",
        args_model=CustomerGetArgs,
    )

    def resource_attributes(self, args: CustomerGetArgs, ctx: ToolContext) -> Dict[str, Any]:
        with ctx.sandbox.connect() as conn:
            row = conn.execute("SELECT id FROM customers WHERE id = ?", (args.customer_id,)).fetchone()
        return {"exists": row is not None, "owner_customer_id": args.customer_id}

    async def _run(self, args: CustomerGetArgs, ctx: ToolContext) -> ToolOutput:
        with ctx.sandbox.connect() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (args.customer_id,)).fetchone()
        if row is None:
            raise ToolError("not_found", "Customer not found")
        data = _row_to_dict(row)
        allowed = ctx.grant.get("fields")
        if allowed:
            data = {k: v for k, v in data.items() if k in allowed}
        text = "Customer " + ", ".join(f"{k}={v}" for k, v in data.items())
        return ToolOutput(data=data, text=text, classification="confidential", summary={"fields": sorted(data)})


# --------------------------------------------------------------------------- db.query
Scalar = Union[str, int, float]


class DbQueryArgs(StrictArgs):
    table: str = Field(min_length=1, max_length=40)
    columns: List[str] = Field(default_factory=list, max_length=12)
    filters: Dict[str, Scalar] = Field(default_factory=dict, max_length=5)
    limit: int = Field(default=20, ge=1, le=50)

    @field_validator("table")
    @classmethod
    def _table_ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError("table must be a simple identifier")
        return v

    @field_validator("columns")
    @classmethod
    def _col_idents(cls, v: List[str]) -> List[str]:
        for c in v:
            if not _IDENT.match(c):
                raise ValueError("columns must be simple identifiers")
        return v

    @field_validator("filters")
    @classmethod
    def _filter_idents(cls, v: Dict[str, Scalar]) -> Dict[str, Scalar]:
        for c, val in v.items():
            if not _IDENT.match(c):
                raise ValueError("filter keys must be simple identifiers")
            if isinstance(val, str) and len(val) > 120:
                raise ValueError("filter values are limited to 120 characters")
        return v


class DbQueryTool(ProtectedTool):
    spec = ToolSpec(
        name="db.query",
        description="Read rows from a permitted table using equality filters. No raw SQL.",
        operation="read",
        sensitivity="medium",
        resource_kind="table",
        args_model=DbQueryArgs,
    )

    def resource_attributes(self, args: DbQueryArgs, ctx: ToolContext) -> Dict[str, Any]:
        cols = ctx.sandbox.table_columns(args.table)
        return {"table": args.table, "table_exists": bool(cols), "table_columns": cols}

    async def _run(self, args: DbQueryArgs, ctx: ToolContext) -> ToolOutput:
        valid = ctx.sandbox.table_columns(args.table)
        if not valid:
            raise ToolError("not_found", "Unknown table")
        cols = args.columns or [c for c in valid if c in (ctx.grant.get("tables", {}).get(args.table) or valid)]
        for c in list(cols) + list(args.filters):
            if c not in valid:
                raise ToolError("tool_error", "Unknown column")
        where = " AND ".join(f"{k} = ?" for k in args.filters) or "1=1"
        sql = f"SELECT {', '.join(cols)} FROM {args.table} WHERE {where} LIMIT ?"
        with ctx.sandbox.connect() as conn:
            rows = [_row_to_dict(r) for r in conn.execute(sql, [*args.filters.values(), args.limit])]
        text = f"{len(rows)} row(s) from {args.table}: " + "; ".join(
            ", ".join(f"{k}={v}" for k, v in r.items()) for r in rows
        )
        cls = "confidential" if args.table in ("customers", "payment_methods", "employees") else "internal"
        return ToolOutput(data=rows, text=text, classification=cls, summary={"rows": len(rows), "table": args.table})


# --------------------------------------------------------------------------- db.update
class DbUpdateArgs(StrictArgs):
    table: str = Field(min_length=1, max_length=40)
    record_id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Z0-9-]+$")
    fields: Dict[str, Scalar] = Field(min_length=1, max_length=5)

    @field_validator("table")
    @classmethod
    def _table_ident(cls, v: str) -> str:
        if not _IDENT.match(v):
            raise ValueError("table must be a simple identifier")
        return v

    @field_validator("fields")
    @classmethod
    def _field_idents(cls, v: Dict[str, Scalar]) -> Dict[str, Scalar]:
        for c, val in v.items():
            if not _IDENT.match(c) or c == "id":
                raise ValueError("field names must be simple identifiers and cannot be 'id'")
            if isinstance(val, str) and len(val) > 200:
                raise ValueError("field values are limited to 200 characters")
        return v


class DbUpdateTool(ProtectedTool):
    spec = ToolSpec(
        name="db.update",
        description="Update fields on one record in a permitted table.",
        operation="write",
        sensitivity="high",
        resource_kind="table",
        args_model=DbUpdateArgs,
        sensitive_fields=("fields",),
    )

    def resource_attributes(self, args: DbUpdateArgs, ctx: ToolContext) -> Dict[str, Any]:
        cols = ctx.sandbox.table_columns(args.table)
        return {"table": args.table, "table_exists": bool(cols), "table_columns": cols}

    async def _run(self, args: DbUpdateArgs, ctx: ToolContext) -> ToolOutput:
        valid = ctx.sandbox.table_columns(args.table)
        if not valid or "id" not in valid:
            raise ToolError("not_found", "Unknown table")
        for c in args.fields:
            if c not in valid:
                raise ToolError("tool_error", "Unknown column")
        sets = ", ".join(f"{k} = ?" for k in args.fields)
        with ctx.sandbox.connect() as conn:
            cur = conn.execute(f"UPDATE {args.table} SET {sets} WHERE id = ?", [*args.fields.values(), args.record_id])
            conn.commit()
            changed = cur.rowcount
        if changed == 0:
            raise ToolError("not_found", "Record not found")
        return ToolOutput(
            data={"updated": changed},
            text=f"Updated {changed} record in {args.table}.",
            classification="internal",
            summary={"table": args.table, "record_id": args.record_id, "fields": sorted(args.fields)},
        )


def optional_str(v: Optional[str]) -> str:
    return v or ""
