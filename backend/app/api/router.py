"""Central API router combining all resource route modules."""
from fastapi import APIRouter

from backend.app.api.routes import activities, data, history, plan

api_router = APIRouter(prefix="/api")

api_router.include_router(data.router)
api_router.include_router(plan.router)
api_router.include_router(activities.router)
api_router.include_router(history.router)
