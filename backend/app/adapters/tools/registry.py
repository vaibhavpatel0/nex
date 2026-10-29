"""Catalogue of protected tools.

``TOOL_SPECS`` is public metadata (safe to expose). ``_build_implementations`` is only
called by the SENTINEL gateway, which keeps the instances private.
"""
from __future__ import annotations

from typing import Dict

from app.adapters.tools.base import ProtectedTool, ToolSpec
from app.adapters.tools.email_tools import DraftTool, InboxTool, SendTool
from app.adapters.tools.file_tools import FileReadTool, FileSearchTool
from app.adapters.tools.service_tools import AdjustStockTool, FetchTool, StockTool
from app.adapters.tools.store_tools import CustomerGetTool, DbQueryTool, DbUpdateTool, OrderLookupTool

_TOOL_CLASSES = (
    OrderLookupTool,
    CustomerGetTool,
    DbQueryTool,
    DbUpdateTool,
    FileSearchTool,
    FileReadTool,
    InboxTool,
    DraftTool,
    SendTool,
    StockTool,
    AdjustStockTool,
    FetchTool,
)

TOOL_SPECS: Dict[str, ToolSpec] = {cls.spec.name: cls.spec for cls in _TOOL_CLASSES}


def _build_implementations() -> Dict[str, ProtectedTool]:
    return {cls.spec.name: cls() for cls in _TOOL_CLASSES}
