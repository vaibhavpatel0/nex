"""FastAPI application for the e-commerce agent platform (storefront API, chat, Agent Workbench)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update

from shop.agent.events import bus
from shop.agent.sentinel_client import SentinelUnavailable, sentinel
from shop.agent.tools import registration_payload
from shop.api import agent_api, store as store_api
from shop.config import PLATFORM_ROOT, VERSION, get_settings
from shop.db import init_engine, schema_ready, session_scope, utcnow
from shop.models import AgentRun

log = logging.getLogger("shop")
registration = {"status": "pending", "detail": "not attempted yet", "at": None}


async def _register_tools_forever() -> None:
    """Register tool definitions with SENTINEL; retry until AEGIS is reachable."""
    delay = 2.0
    while True:
        try:
            res = await sentinel.register_tools(registration_payload())
            registration.update(status="ok", detail=res, at=utcnow().isoformat())
            log.info("registered tools with SENTINEL: %s", res)
            return
        except SentinelUnavailable as exc:
            registration.update(status="failed", detail=str(exc), at=utcnow().isoformat())
            await asyncio.sleep(delay)
            delay = min(60.0, delay * 2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    logging.basicConfig(level=s.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.sandbox_dir.mkdir(parents=True, exist_ok=True)
    init_engine(s.resolved_database_url)
    if not schema_ready():
        raise RuntimeError("Store database is not initialised. Run `make init` in ecommerce-agent-platform.")
    bus.bind(asyncio.get_running_loop())
    with session_scope() as db:
        n = db.execute(update(AgentRun).where(AgentRun.status.notin_(("COMPLETED", "FAILED")))
                       .values(status="FAILED", error_kind="interrupted", error="The backend stopped during this run",
                               ended_at=utcnow())).rowcount
    if n:
        log.warning("%s interrupted agent run(s) marked FAILED", n)
    task = asyncio.create_task(_register_tools_forever())
    yield
    task.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="E-commerce Agent Platform API", version=VERSION, lifespan=lifespan, docs_url="/api/docs",
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origin_list, allow_credentials=False,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def headers(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > 512 * 1024:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "Request validation failed",
                             "errors": [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()[:10]]},
                            status_code=422)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": VERSION, "sentinel_registration": registration["status"]}

    @app.get("/api/workbench/registration")
    def reg():
        return registration

    app.include_router(store_api.router, prefix="/api")
    app.include_router(agent_api.router, prefix="/api")

    dist = Path(PLATFORM_ROOT / "frontend" / "dist")
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            target = (dist / path).resolve()
            if path and target.is_file() and dist.resolve() in target.parents:
                return FileResponse(target)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
