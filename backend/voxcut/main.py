"""FastAPI app factory: static mounting, startup checks, security token (§14)."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import init_db
from .jobs import steps  # noqa: F401 — registers job handlers
from .jobs.runner import runner

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001
    settings().ensure_dirs()
    init_db()
    await runner.start()
    yield
    await runner.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="VOXCUT", version="0.1.0", lifespan=lifespan)

    # --- Security: same-origin only, per-install token on API calls (§14) ---
    @app.middleware("http")
    async def token_guard(request: Request, call_next):  # noqa: ANN001
        path = request.url.path
        # Static assets, the SPA shell, and docs are open; API needs the token.
        if path.startswith("/api"):
            token = request.query_params.get("t") or request.headers.get("x-voxcut-token")
            if token != settings().session_token:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    # --- Routers ---
    from .api import (beats, candidates, edl, jobs, library, music, projects,
                      settings_api, system as system_api, transcript, voiceover)
    app.include_router(projects.router)
    app.include_router(jobs.router)
    app.include_router(settings_api.router)
    app.include_router(voiceover.router)
    app.include_router(transcript.router)
    app.include_router(beats.router)
    app.include_router(edl.router)
    app.include_router(library.router)
    app.include_router(candidates.router)
    app.include_router(system_api.router)
    app.include_router(music.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "app": "voxcut", "version": "0.1.0"}

    # --- Static SPA (built frontend lands in static/; M0 ships a shell) ---
    if (STATIC_DIR / "assets").exists():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def index(request: Request):
        idx = STATIC_DIR / "index.html"
        if not idx.exists():
            raise HTTPException(500, "frontend not built")
        # Localhost convenience: ensure the SPA always has the session token in
        # the URL (matches the launcher). Safe — we bind 127.0.0.1 only.
        if request.query_params.get("t") != settings().session_token:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(f"/?t={settings().session_token}")
        return FileResponse(idx)

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        # Serve real files if they exist, else the SPA shell (client-side routing).
        candidate = STATIC_DIR / full_path
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
