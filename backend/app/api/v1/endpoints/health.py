from datetime import datetime, timezone
from fastapi import APIRouter
from pydantic import BaseModel
from app.core.config import settings
from app.services.ai.factory import get_ai_provider

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    active_provider: str
    active_model: str
    timestamp: str


@router.get("/health", response_model=HealthResponse)
async def get_health():
    """
    Health check endpoint returning system status, active AI provider, and configured model.
    """
    provider = get_ai_provider()
    return HealthResponse(
        status="healthy",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        environment=settings.ENVIRONMENT,
        active_provider=provider.provider_name,
        active_model=settings.resolved_model,
        timestamp=datetime.now(timezone.utc).isoformat()
    )
