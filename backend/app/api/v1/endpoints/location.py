import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Header, status
from pydantic import BaseModel, Field

from app.services.identity import DEFAULT_LOCAL_USER_ID, default_identity_manager
from app.services.location import (
    LocationSnapshot,
    LocationManager,
    get_location_manager_dependency,
)

logger = logging.getLogger("aurora.api.location")
router = APIRouter()


class LocationResponse(BaseModel):
    latitude: float
    longitude: float
    accuracy_meters: float
    captured_at: datetime
    source: str
    user_id: str


class LocationReportRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    accuracy_meters: float = Field(..., ge=0.0, description="Accuracy in meters")
    captured_at: Optional[datetime] = Field(default=None, description="Optional UTC capture timestamp")
    source: Optional[str] = Field(default="ios_core_location", description="Source provider identifier")


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


@router.get("/current", response_model=LocationResponse)
async def get_current_location(
    user_id: str = Depends(get_current_user_id),
    manager: LocationManager = Depends(get_location_manager_dependency),
):
    """
    Retrieve the active user's current device location snapshot.
    Strictly ephemeral: never persisted to database.
    Requires PermissionType.LOCATION to be AUTHORIZED.
    """
    snapshot, reason = await manager.get_current_location(user_id=user_id)

    if snapshot is None:
        if reason == "unauthorized":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Location access is not authorized. The user must grant location permission.",
            )
        elif reason == "expired":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Current location snapshot has expired. An updated observation is required.",
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Current location is currently unavailable.",
            )

    return LocationResponse(
        latitude=snapshot.latitude,
        longitude=snapshot.longitude,
        accuracy_meters=snapshot.accuracy_meters,
        captured_at=snapshot.captured_at,
        source=snapshot.source,
        user_id=snapshot.user_id,
    )


@router.post("/current", response_model=LocationResponse, status_code=status.HTTP_201_CREATED)
async def report_current_location(
    request: LocationReportRequest,
    user_id: str = Depends(get_current_user_id),
    manager: LocationManager = Depends(get_location_manager_dependency),
):
    """
    Receives an ephemeral location snapshot reported by the authorized client.
    Stores strictly in-memory without disk or database persistence.
    """
    is_auth = await manager.is_location_authorized(user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot record location: PermissionType.LOCATION is not authorized for this user.",
        )

    captured_at = request.captured_at or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        captured_at = captured_at.replace(tzinfo=timezone.utc)
    else:
        captured_at = captured_at.astimezone(timezone.utc)

    snapshot = LocationSnapshot(
        latitude=request.latitude,
        longitude=request.longitude,
        accuracy_meters=request.accuracy_meters,
        captured_at=captured_at,
        source=request.source or "ios_core_location",
        user_id=user_id,
    )

    saved = manager.set_current_location(snapshot, user_id=user_id)

    return LocationResponse(
        latitude=saved.latitude,
        longitude=saved.longitude,
        accuracy_meters=saved.accuracy_meters,
        captured_at=saved.captured_at,
        source=saved.source,
        user_id=saved.user_id,
    )
