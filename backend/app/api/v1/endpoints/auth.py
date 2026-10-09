import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.services.identity.auth import (
    ProvisionedCredential,
    SQLiteCredentialStore,
    get_credential_store_dependency,
    provision_credential,
)

router = APIRouter()


class ProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provisioning_secret: str = Field(..., min_length=1, max_length=80)


class ProvisionUser(BaseModel):
    user_id: str


class ProvisionDevice(BaseModel):
    device_id: str


class ProvisionResponse(BaseModel):
    credential: str
    user: ProvisionUser
    device: ProvisionDevice


@router.post("/provision", response_model=ProvisionResponse, status_code=201)
async def provision_installation(
    request: ProvisionRequest,
    store: SQLiteCredentialStore = Depends(get_credential_store_dependency),
):
    result: ProvisionedCredential = await provision_credential(request.provisioning_secret, store)
    return ProvisionResponse(
        credential=result.credential,
        user=ProvisionUser(user_id=result.user.user_id),
        device=ProvisionDevice(device_id=result.device.device_id),
    )


@router.post("/dev-bootstrap", response_model=ProvisionResponse, status_code=201)
async def dev_bootstrap_installation(
    request: Request,
    store: SQLiteCredentialStore = Depends(get_credential_store_dependency),
):
    """
    Automated development bootstrap endpoint strictly restricted to development mode and local loopback.
    Issues and redeems a single-use provisioning secret without requiring manual copy-paste.
    """
    if settings.ENVIRONMENT != "development" or not settings.DEBUG:
        raise HTTPException(status_code=404, detail="Not found")

    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "localhost", "testclient"):
        raise HTTPException(
            status_code=403,
            detail="Development bootstrap is restricted to local loopback access."
        )

    secret = await store.issue_provisioning()
    digest = hashlib.sha256(secret.encode("ascii")).hexdigest()
    result = await store.redeem_provisioning(digest)
    if not result:
        raise HTTPException(status_code=500, detail="Failed to bootstrap development credential.")

    return ProvisionResponse(
        credential=result.credential,
        user=ProvisionUser(user_id=result.user.user_id),
        device=ProvisionDevice(device_id=result.device.device_id),
    )

