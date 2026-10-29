"""FastAPI application factory for AEGIS AI + SENTINEL."""
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

from app.api import aegis_routes, agent_routes, auth_routes, policy_routes, runtime_routes, system_routes
from app.config.settings import REPO_ROOT, SOFTWARE_VERSION, get_settings
from app.database.base import init_engine, schema_is_current
from app.middleware.security import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from app.websocket import routes as ws_routes
from app.websocket.event_bus import event_bus

log = logging.getLogger("aegis")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.sandbox_dir.mkdir(parents=True, exist_ok=True)
    init_engine(settings.resolved_database_url)
    if not schema_is_current():
        raise RuntimeError("Database is not initialised. Run `make init` (python -m app.cli init) first.")
    event_bus.bind_loop(asyncio.get_running_loop())
    from app.aegis.scenario_runner import runner
    recovered = runner.recover_interrupted()
    if recovered:
        event_bus.publish("system.recovered", f"{recovered} interrupted run(s) marked failed after restart", severity="warning")
    event_bus.publish("system.started", f"Backend started (v{SOFTWARE_VERSION})")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="AEGIS AI + SENTINEL API",
        version=SOFTWARE_VERSION,
        description="Security testing (AEGIS) and runtime tool-call enforcement (SENTINEL) for AI agents.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(RateLimitMiddleware, per_minute=1200)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"], allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Do not echo submitted values back; return locations and messages only.
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "invalid"), "type": e.get("type")} for e in exc.errors()[:10]]
        return JSONResponse({"detail": "Request validation failed", "errors": errs}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    for module in (system_routes, auth_routes, agent_routes, policy_routes, aegis_routes, runtime_routes):
        app.include_router(module.router, prefix="/api/v1")
    app.include_router(ws_routes.router)

    # Serve the built frontend when present (single-process deployment / Docker).
    dist = Path(REPO_ROOT / "frontend" / "dist")
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
