"""Health check: answers without signing in, and reveals nothing about the training data."""
from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"app": "marathon", "status": "ok"}
