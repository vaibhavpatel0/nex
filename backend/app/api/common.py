"""Shared API helpers: pagination, not-found handling, serialisation."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Type, TypeVar

from fastapi import HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0, le=1_000_000)) -> None:
        self.limit = limit
        self.offset = offset


def paginate(db: Session, stmt: Select, page: Page) -> "tuple[List[Any], int]":
    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    rows = db.execute(stmt.limit(page.limit).offset(page.offset)).scalars().all()
    return list(rows), int(total)


def page_body(items: Sequence[Any], total: int, page: Page) -> Dict[str, Any]:
    return {"items": list(items), "total": total, "limit": page.limit, "offset": page.offset}


def get_or_404(db: Session, model: Type[T], key: Any, what: str = "Resource") -> T:
    obj = db.get(model, key) if isinstance(key, str) and len(key) <= 64 else None
    if obj is None:
        # Uniform 404 so that existence of other objects cannot be probed.
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return obj


def clean_setup(setup: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {k: v for k, v in (setup or {}).items() if not k.startswith("_")}
