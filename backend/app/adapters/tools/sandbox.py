"""Per-run sandboxes built from the bundled fixtures.

A sandbox is a directory containing a copy of the fixture documents, a fresh SQLite
store database, a simulated inbox, an outbox and inventory state. Tools operate only on
sandbox contents. Nothing here touches the network.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"

_SCHEMA = {
    "customers": "id TEXT PRIMARY KEY, name TEXT, email TEXT, tier TEXT, tax_id TEXT, notes TEXT",
    "orders": "id TEXT PRIMARY KEY, customer_id TEXT REFERENCES customers(id), status TEXT, total REAL, items TEXT, ship_to TEXT",
    "products": "sku TEXT PRIMARY KEY, name TEXT, price REAL",
    "payment_methods": "id TEXT PRIMARY KEY, customer_id TEXT, card_number TEXT, marker TEXT",
    "employees": "id TEXT PRIMARY KEY, name TEXT, role TEXT, salary_usd INTEGER, marker TEXT",
}

_INITIAL_STOCK = {"SKU-100": 42, "SKU-101": 0, "SKU-102": 7, "SKU-103": 15}


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ creation
    @classmethod
    def create(cls, root: Path) -> "Sandbox":
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True)
        shutil.copytree(FIXTURES_DIR / "documents", root / "documents")
        store = json.loads((FIXTURES_DIR / "store.json").read_text())
        conn = sqlite3.connect(root / "store.db")
        try:
            for table, cols in _SCHEMA.items():
                conn.execute(f"CREATE TABLE {table} ({cols})")
                for row in store[table]:
                    keys = list(row.keys())
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                        [row[k] for k in keys],
                    )
            conn.commit()
        finally:
            conn.close()
        shutil.copy(FIXTURES_DIR / "inbox.json", root / "inbox.json")
        shutil.copy(FIXTURES_DIR / "web_pages.json", root / "web_pages.json")
        (root / "inventory.json").write_text(json.dumps(_INITIAL_STOCK))
        (root / "outbox.jsonl").write_text("")
        (root / "drafts.jsonl").write_text("")
        (root / "SANDBOX_README.txt").write_text(
            "AEGIS sandbox. Demo fixture data only. Safe to delete.\n"
        )
        return cls(root)

    @classmethod
    def open_or_create(cls, root: Path) -> "Sandbox":
        if (root / "store.db").exists():
            return cls(root)
        return cls.create(root)

    # ------------------------------------------------------------------ store
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.root / "store.db", timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def table_columns(self, table: str) -> List[str]:
        if table not in _SCHEMA:
            return []
        with self.connect() as conn:
            return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]

    @staticmethod
    def known_tables() -> List[str]:
        return list(_SCHEMA.keys())

    # ------------------------------------------------------------------ json state
    def read_json(self, name: str) -> Any:
        return json.loads((self.root / name).read_text())

    def write_json(self, name: str, data: Any) -> None:
        with self._lock:
            (self.root / name).write_text(json.dumps(data))

    def append_jsonl(self, name: str, record: Dict[str, Any]) -> None:
        with self._lock:
            with open(self.root / name, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")

    def read_jsonl(self, name: str) -> List[Dict[str, Any]]:
        path = self.root / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
