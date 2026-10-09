import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Header, Query, status
from pydantic import BaseModel, Field

from app.services.identity import DEFAULT_LOCAL_USER_ID, default_identity_manager
from app.services.contacts import (
    ContactSnapshot,
    ContactManager,
    get_contact_manager_dependency,
    MAX_SNAPSHOT_CONTACTS,
    MAX_QUERY_RESULTS,
    DEFAULT_QUERY_RESULTS,
    MAX_QUERY_LENGTH,
)

logger = logging.getLogger("aurora.api.contacts")
router = APIRouter()


class ContactResponseItem(BaseModel):
    contact_id: str
    display_name: str
    phone_numbers: List[str] = Field(default_factory=list)
    email_addresses: List[str] = Field(default_factory=list)
    organization: Optional[str] = None
    source: str = "ios_contacts"
    user_id: str


class ContactSearchResponse(BaseModel):
    status: str = "success"
    count: int
    contacts: List[ContactResponseItem]
    query: Optional[str] = None


class ContactIngestItem(BaseModel):
    contact_id: str = Field(..., description="Unique contact ID from device source")
    display_name: str = Field(..., description="Full or primary display name")
    phone_numbers: List[str] = Field(default_factory=list, description="List of phone numbers")
    email_addresses: List[str] = Field(default_factory=list, description="List of email addresses")
    organization: Optional[str] = None
    source: Optional[str] = "ios_contacts"
    user_id: Optional[str] = None


class ContactSnapshotIngestRequest(BaseModel):
    contacts: List[ContactIngestItem] = Field(default_factory=list)
    user_id: Optional[str] = Field(default=None, description="Optional caller-supplied user_id for validation")


def get_current_user_id(
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
) -> str:
    """Derives target identity from authenticated header or default local user."""
    if x_user_id and x_user_id.strip():
        return x_user_id.strip()
    return default_identity_manager.get_current_user().user_id


@router.get("", response_model=ContactSearchResponse)
async def search_contacts(
    query: Optional[str] = Query(default=None, max_length=MAX_QUERY_LENGTH, description="Optional search term"),
    max_results: int = Query(default=DEFAULT_QUERY_RESULTS, ge=1, le=MAX_QUERY_RESULTS, description="Maximum number of contacts"),
    user_id: str = Depends(get_current_user_id),
    manager: ContactManager = Depends(get_contact_manager_dependency),
):
    """
    Search and retrieve read-only contacts for the active user.
    Requires PermissionType.CONTACTS to be AUTHORIZED.
    """
    is_auth = await manager.is_contacts_authorized(user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Contacts access is not authorized. The user must grant contacts permission.",
        )

    try:
        contacts, reason = await manager.get_contacts(
            query=query,
            max_results=max_results,
            user_id=user_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    items = [
        ContactResponseItem(
            contact_id=c.contact_id,
            display_name=c.display_name,
            phone_numbers=c.phone_numbers,
            email_addresses=c.email_addresses,
            organization=c.organization,
            source=c.source,
            user_id=c.user_id,
        )
        for c in contacts
    ]

    return ContactSearchResponse(
        status="success",
        count=len(items),
        contacts=items,
        query=query,
    )


@router.post("/snapshot", response_model=ContactSearchResponse, status_code=status.HTTP_201_CREATED)
async def ingest_contacts_snapshot(
    request: ContactSnapshotIngestRequest,
    user_id: str = Depends(get_current_user_id),
    manager: ContactManager = Depends(get_contact_manager_dependency),
):
    """
    Ingest a bounded ephemeral contact snapshot relevant to the current explicit request.
    CRITICAL SECURITY CONTRACT:
    - This endpoint does NOT create, modify, or delete contacts on the device or disk.
    - Active Aurora identity determines ownership. Client cannot inject contacts into another user's cache.
    - Cross-user contact injection is strictly rejected with 403 Forbidden.
    - Snapshot represents bounded, ephemeral contacts relevant to the explicit request, NOT the entire address book.
    """
    is_auth = await manager.is_contacts_authorized(user_id=user_id)
    if not is_auth:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot ingest contacts snapshot: PermissionType.CONTACTS is not authorized for this user.",
        )

    # Validate caller-supplied user_id against active identity
    if request.user_id and request.user_id.strip() and request.user_id.strip() != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cross-user contact injection rejected: request user_id '{request.user_id}' does not match authenticated user '{user_id}'.",
        )

    # Validate individual items
    domain_contacts: List[ContactSnapshot] = []
    for item in request.contacts:
        if item.user_id and item.user_id.strip() and item.user_id.strip() != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Cross-user contact injection rejected: contact user_id '{item.user_id}' does not match authenticated user '{user_id}'.",
            )

        try:
            c = ContactSnapshot(
                contact_id=item.contact_id,
                display_name=item.display_name,
                phone_numbers=item.phone_numbers,
                email_addresses=item.email_addresses,
                organization=item.organization,
                source=item.source or "ios_contacts",
                user_id=user_id,
            )
            domain_contacts.append(c)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid contact record: {exc}",
            )

    try:
        saved = await manager.set_contacts(domain_contacts, user_id=user_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )

    items = [
        ContactResponseItem(
            contact_id=c.contact_id,
            display_name=c.display_name,
            phone_numbers=c.phone_numbers,
            email_addresses=c.email_addresses,
            organization=c.organization,
            source=c.source,
            user_id=c.user_id,
        )
        for c in saved
    ]

    return ContactSearchResponse(status="success", count=len(items), contacts=items)
