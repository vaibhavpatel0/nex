"""Command-line utilities.

  python -m app.cli init                 apply migrations, seed built-ins, create the first admin
  python -m app.cli create-user NAME ROLE
  python -m app.cli reset-password NAME   set a new password (prompted, not echoed) and sign out all sessions
  python -m app.cli run SUITE AGENT_KEY   run a suite without the web UI and print outcomes
  python -m app.cli create-service-token NAME --agents a,b --namespaces shop [--scopes ...]
                                          create or rotate a token for another application (printed once)
  python -m app.cli reset-password USERNAME
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

from app.config.settings import BACKEND_DIR, get_settings


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def migrate(url: str) -> None:
    command.upgrade(alembic_config(url), "head")


def cmd_init(args: argparse.Namespace) -> int:
    from app.database.base import db_session, init_engine
    from app.seed.loader import ensure_admin, seed_all

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.sandbox_dir.mkdir(parents=True, exist_ok=True)
    migrate(s.resolved_database_url)
    init_engine(s.resolved_database_url)
    with db_session() as db:
        created = seed_all(db)
    print("Database ready:", s.resolved_database_url.split("///")[-1])
    print("Seeded:", ", ".join(f"{k}={v}" for k, v in created.items()))
    password = os.environ.get("AEGIS_ADMIN_PASSWORD")
    generated = False
    if not password:
        password = secrets.token_urlsafe(12)
        generated = True
    username = os.environ.get("AEGIS_ADMIN_USERNAME", "admin")
    with db_session() as db:
        uid = ensure_admin(db, username, password)
    if uid:
        print(f"Created admin user '{username}'.")
        if generated:
            print(f"Generated password (shown once, store it safely): {password}")
    else:
        print("An admin user already exists; no credentials changed.")
    return 0


def cmd_create_user(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.core.timeutil import new_id
    from app.database.base import db_session, init_engine
    from app.models import User
    from app.security.authentication import hash_password

    if args.role not in ("admin", "operator", "viewer"):
        print("role must be admin, operator or viewer", file=sys.stderr)
        return 2
    pw = os.environ.get("AEGIS_NEW_USER_PASSWORD") or getpass.getpass("Password (min 12 chars): ")
    if len(pw) < 12:
        print("Password must be at least 12 characters. Nothing was created.", file=sys.stderr)
        return 1
    init_engine(get_settings().resolved_database_url)
    with db_session() as db:
        if db.execute(select(User).where(User.username == args.username)).first():
            print("user exists", file=sys.stderr)
            return 1
        db.add(User(id=new_id("usr"), username=args.username, password_hash=hash_password(pw), role=args.role))
    print("created")
    return 0


def cmd_reset_password(args: argparse.Namespace) -> int:
    from sqlalchemy import select, update

    from app.core.timeutil import utcnow
    from app.database.base import db_session, init_engine
    from app.models import ApiSession, User
    from app.security.authentication import hash_password

    pw = os.environ.get("AEGIS_NEW_USER_PASSWORD")
    if not pw:
        pw = getpass.getpass("New password (min 12 chars): ")
        if pw != getpass.getpass("Repeat new password: "):
            print("passwords do not match", file=sys.stderr)
            return 1
    try:
        new_hash = hash_password(pw)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    init_engine(get_settings().resolved_database_url)
    with db_session() as db:
        user = db.execute(select(User).where(User.username == args.username)).scalar_one_or_none()
        if user is None:
            print("no such user", file=sys.stderr)
            return 1
        user.password_hash = new_hash
        user.disabled = False
        db.execute(update(ApiSession).where(ApiSession.user_id == user.id, ApiSession.revoked_at.is_(None))
                   .values(revoked_at=utcnow()))
    print(f"Password updated for '{args.username}'. Existing sessions were signed out.")
    return 0


DEFAULT_SERVICE_SCOPES = "sentinel:evaluate,sentinel:ingest,tools:register,read,tests:run,approvals:decide"


def cmd_service_token(args: argparse.Namespace) -> int:
    from app.database.base import db_session, init_engine
    from app.security.authentication import create_service_token

    init_engine(get_settings().resolved_database_url)
    with db_session() as db:
        try:
            token = create_service_token(db, args.name, [x.strip() for x in args.scopes.split(",") if x.strip()],
                                         [x.strip() for x in args.agents.split(",") if x.strip()],
                                         [x.strip() for x in args.namespaces.split(",") if x.strip()], "cli")
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
    if args.quiet:
        print(token)
    else:
        print(f"Service token for '{args.name}' (shown once; any previous token for this name is now invalid):")
        print(token)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from sqlalchemy import select

    from app.aegis.scenario_runner import runner
    from app.database.base import db_session, init_engine
    from app.models import Agent, ScenarioResult

    init_engine(get_settings().resolved_database_url)
    with db_session() as db:
        agent = db.execute(select(Agent).where(Agent.key == args.agent)).scalar_one_or_none()
        if agent is None:
            print("unknown agent", file=sys.stderr)
            return 2
        agent_id = agent.id
    run_id = runner.create_run(agent_id=agent_id, suite_key=args.suite, scenario_keys=None, label=f"CLI {args.suite}",
                               triggered_by="cli")
    asyncio.run(runner.run_now(run_id))
    with db_session() as db:
        for r in db.execute(select(ScenarioResult).where(ScenarioResult.run_id == run_id).order_by(ScenarioResult.started_at)).scalars():
            print(f"{r.scenario_key:8} {r.outcome:13} {r.reason[:110]}")
    print("run:", run_id)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="aegis")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    cu = sub.add_parser("create-user")
    cu.add_argument("username")
    cu.add_argument("role")
    cu.set_defaults(fn=cmd_create_user)
    rp = sub.add_parser("reset-password")
    rp.add_argument("username")
    rp.set_defaults(fn=cmd_reset_password)
    st = sub.add_parser("create-service-token")
    st.add_argument("name")
    st.add_argument("--agents", required=True)
    st.add_argument("--namespaces", default="")
    st.add_argument("--scopes", default=DEFAULT_SERVICE_SCOPES)
    st.add_argument("--quiet", action="store_true")
    st.set_defaults(fn=cmd_service_token)
    r = sub.add_parser("run")
    r.add_argument("suite")
    r.add_argument("agent")
    r.set_defaults(fn=cmd_run)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
