"""Strava connection and sync routes. See backend/app/services/strava.py for how the import works."""
import logging
import secrets
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from backend.app.core.errors import AppError
from backend.app.services import strava

router = APIRouter(prefix="/strava", tags=["strava"])
log = logging.getLogger("marathon.strava")

# One-time values tying Strava's redirect back to a Connect click in this app (OAuth `state`),
# so another site can't complete a connection to *its* Strava account. Kept for 10 minutes.
STATE_SECONDS = 600
_pending: dict[str, float] = {}

# Where the browser lands afterwards: the Activity log, which shows the connection status.
DONE_URL = "/#/log"


@router.get("/status")
def strava_status():
    return strava.status()


@router.get("/connect")
def strava_connect(request: Request):
    now = time.time()
    for key in [k for k, expires in _pending.items() if expires < now]:
        del _pending[key]
    state = secrets.token_urlsafe(24)
    _pending[state] = now + STATE_SECONDS
    return RedirectResponse(strava.authorize_url(str(request.url_for("strava_callback")), state), status_code=303)


@router.get("/callback", name="strava_callback")
def strava_callback(state: str = "", code: Optional[str] = None, scope: str = "", error: Optional[str] = None):
    if _pending.pop(state, 0) < time.time():
        strava.connect_error = "That Strava sign-in link had expired or wasn't started here. Click Connect again."
    elif error or not code:
        strava.connect_error = "Strava access wasn't granted." if error == "access_denied" else f"Strava said: {error}"
    else:
        try:
            strava.connect(code, scope, date.today())  # nothing is imported until "Sync now"
        except AppError as e:
            log.warning("Connecting Strava failed: %s", e.detail)
    return RedirectResponse(DONE_URL, status_code=303)


@router.post("/sync")
def strava_sync():
    return {**strava.status(), "imported": strava.sync()}


@router.post("/disconnect")
def strava_disconnect():
    return strava.disconnect()
