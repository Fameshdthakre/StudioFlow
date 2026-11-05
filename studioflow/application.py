"""Application factory and lifecycle, shared by Uvicorn and isolated tests."""

import ipaddress
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from .api import aplus, auth, catalog, memory, renders, system, usage, library
from .database import initialize
from .jobs import RenderQueue
from .services.rendering import generate_one
from .settings import Settings
from .workspace import Workspace


def create_app(
    settings: Settings | None = None, *, start_queue: bool = True
) -> FastAPI:
    base = Path(__file__).resolve().parents[1]
    workspace = Workspace(
        settings if settings is not None else Settings.load(base), base
    )
    from .services.assets import process_auxiliary

    @asynccontextmanager
    async def lifespan(application):
        for directory in (
            workspace.data,
            workspace.uploads,
            workspace.outputs,
            workspace.brand_logos,
            workspace.staging,
            workspace.exports,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if workspace.settings.database_url:
            from .persistence.connection import check_schema

            await run_in_threadpool(check_schema, workspace.settings.database_url)
        else:
            await run_in_threadpool(initialize, workspace.db_path)
            from .persistence.repository import initialize_preview

            await run_in_threadpool(initialize_preview, workspace)
        from .persistence.repository import ensure_user

        with workspace.db() as c:
            for username in (*workspace.settings.team, "Demo user"):
                ensure_user(c, username)
        queue = RenderQueue(
            workspace.db,
            partial(generate_one, workspace),
            workspace.settings.max_workers,
            platform=True,
            heartbeat_db=workspace.lease_db,
            auxiliary=partial(process_auxiliary, workspace),
            saved_receipt=lambda rid: (
                workspace.staging / f"{rid}.receipt.json"
            ).is_file(),
        )
        application.state.render_queue = queue
        if start_queue:
            await run_in_threadpool(queue.start)
        try:
            yield
        finally:
            if start_queue:
                await run_in_threadpool(queue.stop)

    app = FastAPI(title="Amazon Image Studio", lifespan=lifespan)
    app.state.workspace = workspace

    @app.middleware("http")
    async def restrict_anonymous_demo(request: Request, call_next):
        if workspace.settings.anonymous_demo:
            peer = request.client.host if request.client else ""
            try:
                local_peer = ipaddress.ip_address(peer).is_loopback
            except ValueError:
                local_peer = False
            local_host = request.url.hostname in ("localhost", "127.0.0.1", "::1")
            if not local_peer or not local_host:
                return JSONResponse(
                    status_code=403,
                    content={
                        "detail": "Anonymous demos are available only on localhost. Configure team accounts for network access."
                    },
                )
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            from .services.maintenance import writer

            gate = writer(workspace)
            try:
                await run_in_threadpool(gate.__enter__)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code, content={"detail": exc.detail}
                )
            try:
                return await call_next(request)
            finally:
                await run_in_threadpool(gate.__exit__, None, None, None)
        return await call_next(request)

    app.mount("/static", StaticFiles(directory=base / "static"), name="static")
    for feature in (system, auth, aplus, usage, catalog, memory, renders, library):
        app.include_router(feature.router)
    return app
