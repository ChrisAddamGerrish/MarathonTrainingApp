"""Central API router combining all resource route modules."""
from fastapi import APIRouter, Depends

from backend.app.api.deps import require_login
from backend.app.api.routes import activities, auth, backup, data, health, history, plan, strava

api_router = APIRouter(prefix="/api")

# Open: sign-in and registration themselves, and the health check start.ps1 polls. (Invites check
# for an admin themselves.)
api_router.include_router(auth.router)
api_router.include_router(health.router)

# Everything that reads or changes training data needs a signed-in session.
signed_in = [Depends(require_login)]
api_router.include_router(data.router, dependencies=signed_in)
api_router.include_router(plan.router, dependencies=signed_in)
api_router.include_router(activities.router, dependencies=signed_in)
api_router.include_router(history.router, dependencies=signed_in)
# Each Strava route checks the user itself (the OAuth callback redirects instead of refusing).
api_router.include_router(strava.router)
api_router.include_router(backup.router, dependencies=signed_in)
