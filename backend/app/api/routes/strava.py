"""Strava connection and sync routes. See backend/app/services/strava.py for how the import works."""
import logging
import secrets
import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from backend.app.api.deps import UserDep, signed_in_user
from backend.app.core.errors import AppError
from backend.app.services import strava

router = APIRouter(prefix="/strava", tags=["strava"])
log = logging.getLogger("marathon.strava")

# One-time values tying Strava's redirect back to a Connect click in this app (OAuth `state`),
# so another site can't complete a connection to *its* Strava account: {state: (expires, user id)}.
# Kept for 10 minutes.
STATE_SECONDS = 600
_pending: dict[str, tuple[float, int]] = {}

# Where the browser lands afterwards: the Activity log, which shows the connection status.
DONE_URL = "/#/log"


@router.get("/status")
def strava_status(user: UserDep):
    return strava.status(user.user_id)


@router.get("/connect")
def strava_connect(request: Request, user: UserDep):
    now = time.time()
    for key in [k for k, (expires, _) in _pending.items() if expires < now]:
        del _pending[key]
    state = secrets.token_urlsafe(24)
    _pending[state] = (now + STATE_SECONDS, user.user_id)
    return RedirectResponse(strava.authorize_url(str(request.url_for("strava_callback")), state), status_code=303)


@router.get("/callback", name="strava_callback")
def strava_callback(request: Request, state: str = "", code: Optional[str] = None, scope: str = "",
                    error: Optional[str] = None):
    user = signed_in_user(request)
    if user is None:  # signed out in the meantime: back to the sign-in page
        return RedirectResponse("/", status_code=303)
    expires, started_by = _pending.pop(state, (0, None))
    if expires < time.time() or started_by != user.user_id:
        strava.connect_errors[user.user_id] = ("That Strava sign-in link had expired or wasn't started here. "
                                               "Click Connect again.")
    elif error or not code:
        strava.connect_errors[user.user_id] = ("Strava access wasn't granted." if error == "access_denied"
                                               else f"Strava said: {error}")
    else:
        try:
            strava.connect(user.user_id, code, scope, date.today())  # nothing is imported until "Sync now"
        except AppError as e:
            log.warning("Connecting Strava failed: %s", e.detail)
    return RedirectResponse(DONE_URL, status_code=303)


@router.post("/sync")
def strava_sync(user: UserDep):
    return {**strava.status(user.user_id), "imported": strava.sync(user.tenant)}


@router.post("/disconnect")
def strava_disconnect(user: UserDep):
    return strava.disconnect(user.user_id)
