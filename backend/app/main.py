"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router
from backend.app.core.config import DIST_DIR
from backend.app.core.database import init_db
from backend.app.core.errors import AppError


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Marathon Training", lifespan=lifespan)


@app.exception_handler(AppError)
def handle_app_error(_: Request, error: AppError):
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


# --------------------------------------------------------------------------
# API Routes
# --------------------------------------------------------------------------
app.include_router(api_router)


# --------------------------------------------------------------------------
# Front end SPA serving
# --------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def index():
    if not (DIST_DIR / "index.html").exists():
        return HTMLResponse(
            "<h1>Front end not built</h1>"
            "<p>Run <code>npm install</code> and <code>npm run build</code> in the "
            "<code>frontend</code> folder, then reload. (For development, run "
            "<code>npm run dev</code> there and open http://localhost:5173.)</p>",
            status_code=503,
        )
    # index.html is tiny and must never be stale, or it would point at old hashed bundles.
    return FileResponse(DIST_DIR / "index.html", headers={"Cache-Control": "no-cache"})


# Vite writes hashed JS/CSS bundles to dist/assets. check_dir=False lets the app start (and
# pick the folder up later) even if the front end hasn't been built yet.
app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets", check_dir=False), name="assets")
