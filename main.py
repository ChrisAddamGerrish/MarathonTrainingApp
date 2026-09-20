from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

import planning
import repository
from config import DIST_DIR
from database import get_session, init_db
from errors import AppError
from schemas import ActivityIn, SkipIn


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Marathon Training", lifespan=lifespan)


@app.exception_handler(AppError)
def handle_app_error(_: Request, error: AppError):
    return JSONResponse({"detail": error.detail}, status_code=error.status_code)


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.get("/api/data")
def all_data(session: Session = Depends(get_session)):
    """Everything the front end needs in one round trip (the dataset is tiny)."""
    today = date.today()
    plan = planning.build_plan(repository.plan_rows(session), today)
    activities = repository.activity_rows(session)
    weeks = planning.build_weeks(plan, activities)
    summary = planning.build_summary(plan, activities, weeks, today)
    return {"summary": summary, "weeks": weeks, "plan": plan, "activities": activities}


@app.put("/api/plan/{plan_id}/skip")
def skip_session(plan_id: str, body: Optional[SkipIn] = None, session: Session = Depends(get_session)):
    return repository.skip_session(session, plan_id, body.reason if body else None)


@app.delete("/api/plan/{plan_id}/skip")
def unskip_session(plan_id: str, session: Session = Depends(get_session)):
    return repository.unskip_session(session, plan_id)


@app.post("/api/activities", status_code=201)
def create_activity(body: ActivityIn, session: Session = Depends(get_session)):
    return repository.create_activity(session, body.model_dump())


@app.put("/api/activities/{activity_id}")
def update_activity(activity_id: int, body: ActivityIn, session: Session = Depends(get_session)):
    return repository.update_activity(session, activity_id, body.model_dump())


@app.delete("/api/activities/{activity_id}")
def delete_activity(activity_id: int, session: Session = Depends(get_session)):
    return repository.delete_activity(session, activity_id)


@app.get("/api/history")
def history(
    activity_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=1000),
    session: Session = Depends(get_session),
):
    return repository.history_entries(session, activity_id, limit)


@app.post("/api/history/{history_id}/revert")
def revert_history(history_id: int, session: Session = Depends(get_session)):
    return repository.revert_history(session, history_id)


# --------------------------------------------------------------------------
# Front end
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
