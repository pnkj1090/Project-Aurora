from app.services.identity.models import (
    UserIdentity,
    DeviceIdentity,
    DEFAULT_LOCAL_USER_ID,
)
from app.services.identity.manager import (
    IdentityManager,
    default_identity_manager,
)
from app.services.identity.auth import (
    AuthenticatedPrincipal,
    IdentityResolver,
    SQLiteCredentialStore,
    get_authenticated_principal,
    get_credential_store_dependency,
    get_identity_resolver_dependency,
)

__all__ = [
    "UserIdentity",
    "DeviceIdentity",
    "DEFAULT_LOCAL_USER_ID",
    "IdentityManager",
    "default_identity_manager",
    "AuthenticatedPrincipal",
    "IdentityResolver",
    "SQLiteCredentialStore",
    "get_authenticated_principal",
    "get_credential_store_dependency",
    "get_identity_resolver_dependency",
]
