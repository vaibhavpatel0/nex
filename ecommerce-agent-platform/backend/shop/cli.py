"""Command-line utilities for the e-commerce agent platform.

  python -m shop.cli init            migrate, seed synthetic store data, create demo accounts
  python -m shop.cli reset-password EMAIL
  python -m shop.cli check-model     one real tool-calling request to the configured model
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import secrets
import sys

from alembic import command
from alembic.config import Config

from shop.config import BACKEND_DIR, get_settings


def migrate(url: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")


def cmd_init(_: argparse.Namespace) -> int:
    from shop.db import init_engine, session_scope
    from shop.security import hash_password
    from shop.seed.loader import seed_store

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    migrate(s.resolved_database_url)
    init_engine(s.resolved_database_url)
    password = s.demo_password or secrets.token_urlsafe(9)
    with session_scope() as db:
        created = seed_store(db, password_hash=hash_password(password))
    print("Store database ready:", s.resolved_database_url.split("///")[-1])
    if not created:
        print("Store data already present; accounts and passwords unchanged.")
        return 0
    creds = s.data_dir / "first-run-credentials.txt"
    lines = ["Demo accounts for the synthetic store (local use only).",
             "Customers: asha@shopper.test, rohan@shopper.test, meera@shopper.test",
             "Staff (Agent Workbench): staff@halden.test",
             f"Password for all of them: {password}", ""]
    if not s.demo_password:
        fd = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("\n".join(lines))
    print("\n".join(lines))
    if not s.demo_password:
        print(f"(Also saved to {creds}, readable only by you.)")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from shop.db import init_engine, session_scope
    from shop.models import User
    from shop.security import hash_password

    pw = os.environ.get("SHOP_NEW_PASSWORD") or getpass.getpass("New password (min 8 chars): ")
    try:
        h = hash_password(pw)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    init_engine(get_settings().resolved_database_url)
    with session_scope() as db:
        u = db.execute(select(User).where(User.email == args.email.lower())).scalar_one_or_none()
        if u is None:
            print("No such account", file=sys.stderr)
            return 1
        u.password_hash = h
    print("Password updated.")
    return 0


def cmd_check_model(_: argparse.Namespace) -> int:
    from shop.agent.providers import ModelError, ModelTimeout, ModelUnavailable, build_provider
    from shop.agent.tools import model_tool_specs

    async def go() -> int:
        prov = build_provider()
        st = await prov.status()
        print(f"Provider: {prov.name}  model: {prov.model}  base: {st.get('base_url')}")
        if st.get("models"):
            print("Models listed by the server:", ", ".join(st["models"][:40]))
        print("Listed:", st.get("model_listed"), " tool support reported:", st.get("tool_support"), st.get("error", ""))
        tools = [x for x in model_tool_specs(False) if x["function"]["name"] == "check_inventory"]
        try:
            reply = await prov.chat([{"role": "system", "content": "Call the check_inventory tool for product P-1001."},
                                     {"role": "user", "content": "Is P-1001 in stock?"}], tools)
        except (ModelTimeout, ModelUnavailable, ModelError) as exc:
            print("Inference check FAILED:", type(exc).__name__, exc)
            return 1
        names = [c.name for c in reply.calls]
        print(f"Real inference in {reply.duration_ms:.0f} ms. Tool calls proposed: {names or 'none'}; usage: {reply.usage}")
        if "check_inventory" not in names:
            print("The model answered but did not call the tool. Choose a model with tool-calling support.")
            return 2
        print("OK: the model proposes tool calls.")
        return 0

    return asyncio.run(go())


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="shop")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    rp = sub.add_parser("reset-password")
    rp.add_argument("email")
    rp.set_defaults(fn=cmd_reset_password)
    sub.add_parser("check-model").set_defaults(fn=cmd_check_model)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
