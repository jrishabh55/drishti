"""FastAPI application factory for the TradingAgents API.

Mount layout:
    /                          → static index.html
    /static/*                  → static assets
    /api/config                → frontend dropdown data
    /api/runs                  → REST CRUD-ish over runs
    /api/runs/{id}/events      → SSE stream

Auth: Clerk JWT verification when ``CLERK_JWKS_URL`` is set. Falls back to the
legacy shared-bearer token via ``WEBAPP_AUTH_TOKEN`` when only that is set.
With neither set, the app is fully open (intended for personal/internal use
behind a VPN).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from apps.api.auth import auth_middleware, get_verifier
from apps.api.routes.config import router as config_router
from apps.api.routes.desk import router as desk_router
from apps.api.routes.keys import router as keys_router
from apps.api.routes.relay import router as relay_router
from apps.api.routes.runs import router as runs_router
from apps.api.routes.scanners import router as scanners_router
from apps.api.routes.stream import router as stream_router
from apps.api.jobs.bus import get_bus
from apps.api.jobs.runner import get_runner, shutdown_runner
from apps.api.jobs.store import get_store
from apps.api.desk.loop import desk_loop
from apps.api.scanner.ingest import ingest_loop
from apps.api.scanner.seed import seed_prebuilt
from apps.api.scanner.store import get_scanner_store


logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    # Load .env files BEFORE any provider client is instantiated. Mirrors
    # cli/main.py:10-11.
    load_dotenv()
    load_dotenv(".env.enterprise", override=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Initialise singletons. Doing this in the lifespan (not at import time)
        # keeps unit tests in control of WEBAPP_DB_PATH overrides.
        store = get_store()
        # Any run still queued or running belongs to a process that no longer
        # exists. Sweep BOTH states: a queued row's future died with the previous
        # executor just as surely as a running one, and leaving it queued means it
        # is never picked up and never explained to the user.
        swept = store.sweep_orphaned_runs()
        if swept:
            logger.info("marked %d orphaned run(s) as interrupted", swept)
        bus = get_bus()
        bus.attach_loop(asyncio.get_running_loop())
        get_runner()
        scanner_task = None
        seed_prebuilt(get_scanner_store())
        if os.environ.get("SCANNER_INGEST", "1") != "0":
            scanner_task = asyncio.create_task(ingest_loop(), name="scanner-ingest")
        source_probe_task = None
        if os.environ.get("DESK_SOURCE_PROBE", "0") == "1":
            # Opt-in deployment diagnostic: run the existing server-side data
            # spike without needing an interactive shell in the container.
            from scripts.desk_spike import main as probe_sources
            source_probe_task = asyncio.create_task(asyncio.to_thread(
                probe_sources, ["--rounds", "1", "--label", "dokploy"]), name="desk-source-probe")
        evaluation_task = None
        messaging_task = None
        if os.environ.get('MESSAGING_LOOP', '1') != '0':
            from apps.api.messaging.service import messaging_loop
            messaging_task = asyncio.create_task(messaging_loop(), name='messaging-loop')
        if os.environ.get("EVALUATION_LOOP", "1") != "0":
            from apps.api.evaluation.loop import evaluation_loop
            evaluation_task = asyncio.create_task(evaluation_loop(), name="evaluation-loop")
        kite_task = None
        from apps.api.kite import loop as kite_loop_mod
        kite_loop_mod.install()
        if os.environ.get('KITE_LOOP', '1') != '0':
            kite_task = asyncio.create_task(kite_loop_mod.kite_loop(), name='kite-loop')
        desk_task = None
        if os.environ.get("DESK_NSE_FEEDS", "0") == "1":
            # Off until scripts/desk_spike.py has verified the NSE datasets from
            # this server (spec §3): unverified feeds would only add latency.
            from apps.api.desk import datasets, feeds

            feeds.register_facts_provider(datasets.nse_facts)
        if os.environ.get("DESK_LOOP", "1") != "0":
            desk_task = asyncio.create_task(desk_loop(), name="desk-loop")
        logger.info("webapp ready")
        try:
            yield
        finally:
            if scanner_task is not None:
                scanner_task.cancel()
            if desk_task is not None:
                desk_task.cancel()
            if kite_task is not None:
                kite_task.cancel()
            if evaluation_task is not None:
                evaluation_task.cancel()
            if source_probe_task is not None:
                source_probe_task.cancel()
            if messaging_task is not None:
                messaging_task.cancel()
            await asyncio.gather(*(task for task in (scanner_task, desk_task, kite_task, evaluation_task, source_probe_task, messaging_task)
                                   if task is not None), return_exceptions=True)
            from apps.api.kite import live
            await live.shutdown()
            shutdown_runner()

    app = FastAPI(
        # Client-facing name is "Drishti" (served at /docs and /openapi.json);
        # "TradingAgents" stays internal — repo, python package, upstream fork.
        title="Drishti API",
        version="0.1.0",
        description="Web UI + REST/SSE API for Drishti — multi-agent trading analysis.",
        lifespan=lifespan,
    )

    # CORS — permissive by default for local-only deploys; restrict via env var.
    cors_origins = _split_csv(os.environ.get("WEBAPP_CORS_ORIGINS", "*"))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth: Clerk JWT preferred, legacy shared-bearer fallback, otherwise open.
    # Always installed — populates request.state.user_id for every request, so
    # routes can scope by user without conditional logic. See apps/api/auth.py.
    app.middleware("http")(auth_middleware)
    if get_verifier() is not None:
        logger.info("Clerk auth enabled (CLERK_JWKS_URL configured)")
        from apps.api import clerk_users

        if clerk_users.enabled():
            logger.info("Activation + credits gate enabled (CLERK_SECRET_KEY set)")
        else:
            logger.warning(
                "CLERK_SECRET_KEY not set — activation/credits gate DISABLED; "
                "any signed-in Clerk user can use the API"
            )
    elif os.environ.get("WEBAPP_AUTH_TOKEN", "").strip():
        logger.info("Legacy shared-bearer auth enabled (WEBAPP_AUTH_TOKEN set)")
    else:
        logger.info("Auth disabled — every request runs as 'anonymous'")

    # Routes.
    app.include_router(config_router, prefix="/api")
    app.include_router(keys_router, prefix="/api")
    from apps.api.routes.provider_keys import router as provider_keys_router
    app.include_router(provider_keys_router, prefix="/api")
    from apps.api.routes.notifications import router as notifications_router
    app.include_router(notifications_router, prefix="/api")
    from apps.api.routes.messaging import router as messaging_router
    app.include_router(messaging_router, prefix='/api')
    from apps.api.routes.watch_rules import router as watch_rules_router
    app.include_router(watch_rules_router, prefix="/api")
    from apps.api.routes.evaluation import router as evaluation_router
    app.include_router(evaluation_router, prefix="/api")
    from apps.api.routes.kite import router as kite_router
    app.include_router(kite_router, prefix="/api")
    from apps.api.routes.desk_research import router as desk_research_router
    app.include_router(desk_research_router, prefix="/api")
    app.include_router(runs_router, prefix="/api")
    app.include_router(stream_router, prefix="/api")
    app.include_router(scanners_router, prefix="/api")
    app.include_router(desk_router, prefix="/api")
    # No prefix: these two routes deliberately live on different roots. The
    # websocket is public under /api/relay/ws, while the shim sits on /internal
    # so the frontend's /api/* catch-all proxy does not expose it.
    app.include_router(relay_router)

    # Static frontend.
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        # Container-startup epoch as a cache-bust token. Every container
        # restart yields a new value, which forces browsers to refetch CSS/JS
        # rather than serve a stale, possibly-broken version from cache.
        startup_token = str(int(time.time()))
        index_path = STATIC_DIR / "index.html"

        def _read_index_with_bust() -> str:
            html = index_path.read_text(encoding="utf-8")
            # Append ?v=<token> to references of our own static assets.
            for asset in ("styles.css", "app.js", "marked.min.js", "purify.min.js"):
                src = f"/static/{asset}"
                html = html.replace(src, f"{src}?v={startup_token}")
            return html

        @app.get("/", include_in_schema=False)
        def index() -> Response:
            return HTMLResponse(
                content=_read_index_with_bust(),
                # No-store on the HTML is what makes the cache-bust effective:
                # the browser must fetch a fresh HTML to learn the new ?v=…
                headers={"Cache-Control": "no-store, must-revalidate"},
            )
    else:  # pragma: no cover — only triggers if someone deletes the static dir
        logger.warning("Static directory not found: %s", STATIC_DIR)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    return app


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


# uvicorn entrypoint (`uvicorn apps.api.app:app`).
app = create_app()
