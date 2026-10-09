"""Local installation credentials and request identity resolution."""

import asyncio
import base64
import binascii
import hashlib
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

import aiosqlite
from fastapi import Depends, HTTPException, Request, status

from app.core.config import settings
from app.services.identity.models import DeviceIdentity, UserIdentity, DEFAULT_LOCAL_USER_ID

_TOKEN_RE = re.compile(r"^(?:aurora_cred_|aurora_prov_)([A-Za-z0-9_-]{43})$")
_BEARER_PREFIX = "aurora_cred_"
_PROVISIONING_PREFIX = "aurora_prov_"
_PROVISIONING_LIFETIME = timedelta(minutes=10)


def _new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def _token_hash(token: str) -> Optional[str]:
    match = _TOKEN_RE.fullmatch(token)
    if not match:
        return None
    try:
        raw = base64.urlsafe_b64decode(match.group(1) + "=")
    except (ValueError, binascii.Error):
        return None
    if len(raw) != 32 or base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != match.group(1):
        return None
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    user: UserIdentity
    device: DeviceIdentity


@dataclass(frozen=True)
class ProvisionedCredential:
    credential: str
    user: UserIdentity
    device: DeviceIdentity


@dataclass(frozen=True)
class _CredentialRecord:
    kind: str
    user_id: str
    device_id: str
    created_at: datetime
    expires_at: Optional[datetime]
    consumed_at: Optional[datetime]
    revoked_at: Optional[datetime]


class CredentialStore(Protocol):
    async def resolve_bearer(self, token_hash: str) -> Optional[_CredentialRecord]: ...
    async def redeem_provisioning(self, token_hash: str) -> Optional[ProvisionedCredential]: ...


class SQLiteCredentialStore:
    """Persistent hash-only credential store in the configured Aurora SQLite DB."""

    def __init__(self, db_path: str = settings.DATABASE_PATH):
        self.db_path = db_path
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            parent = os.path.dirname(self.db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            async with aiosqlite.connect(self.db_path) as conn:
                await conn.execute("PRAGMA journal_mode=WAL;")
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS identity_credentials (
                        token_hash TEXT PRIMARY KEY,
                        kind TEXT NOT NULL CHECK (kind IN ('provisioning', 'bearer')),
                        user_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        expires_at TEXT,
                        consumed_at TEXT,
                        revoked_at TEXT
                    );
                """)
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_credentials_user ON identity_credentials(user_id);")
                await conn.execute("CREATE INDEX IF NOT EXISTS idx_identity_credentials_device ON identity_credentials(device_id);")
                await conn.commit()
            self._initialized = True

    async def issue_provisioning(self) -> str:
        await self.initialize()
        secret = _new_token(_PROVISIONING_PREFIX)
        digest = _token_hash(secret)
        assert digest is not None
        now = datetime.now(timezone.utc)
        device_id = f"dev_{uuid.uuid4().hex}"
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.execute(
                "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at, expires_at) VALUES (?, 'provisioning', ?, ?, ?, ?)",
                (digest, DEFAULT_LOCAL_USER_ID, device_id, now.isoformat(), (now + _PROVISIONING_LIFETIME).isoformat()),
            )
            await conn.commit()
        return secret

    async def redeem_provisioning(self, token_hash: str) -> Optional[ProvisionedCredential]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path, timeout=30) as conn:
            await conn.execute("BEGIN IMMEDIATE;")
            try:
                cursor = await conn.execute(
                    "SELECT kind, user_id, device_id, created_at, expires_at, consumed_at, revoked_at FROM identity_credentials WHERE token_hash = ?",
                    (token_hash,),
                )
                row = await cursor.fetchone()
                now = datetime.now(timezone.utc)
                if not row or row[0] != "provisioning" or row[5] is not None or row[6] is not None:
                    await conn.rollback()
                    return None
                expiry = datetime.fromisoformat(row[4]) if row[4] else None
                if expiry is None or now >= expiry:
                    await conn.rollback()
                    return None

                credential = _new_token(_BEARER_PREFIX)
                credential_hash = _token_hash(credential)
                assert credential_hash is not None
                await conn.execute(
                    "UPDATE identity_credentials SET consumed_at = ? WHERE token_hash = ? AND consumed_at IS NULL",
                    (now.isoformat(), token_hash),
                )
                if conn.total_changes < 1:
                    await conn.rollback()
                    return None
                await conn.execute(
                    "INSERT INTO identity_credentials (token_hash, kind, user_id, device_id, created_at) VALUES (?, 'bearer', ?, ?, ?)",
                    (credential_hash, row[1], row[2], now.isoformat()),
                )
                await conn.commit()
                user = UserIdentity(user_id=row[1])
                device = DeviceIdentity(device_id=row[2], user_id=row[1], created_at=datetime.fromisoformat(row[3]), last_active_at=now)
                return ProvisionedCredential(credential=credential, user=user, device=device)
            except Exception:
                await conn.rollback()
                raise

    async def resolve_bearer(self, token_hash: str) -> Optional[_CredentialRecord]:
        await self.initialize()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "SELECT kind, user_id, device_id, created_at, expires_at, consumed_at, revoked_at FROM identity_credentials WHERE token_hash = ?",
                (token_hash,),
            )
            row = await cursor.fetchone()
        if not row:
            return None
        return _CredentialRecord(
            kind=row[0], user_id=row[1], device_id=row[2],
            created_at=datetime.fromisoformat(row[3]),
            expires_at=datetime.fromisoformat(row[4]) if row[4] else None,
            consumed_at=datetime.fromisoformat(row[5]) if row[5] else None,
            revoked_at=datetime.fromisoformat(row[6]) if row[6] else None,
        )

    async def revoke_device(self, device_id: str) -> int:
        await self.initialize()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self.db_path) as conn:
            cursor = await conn.execute(
                "UPDATE identity_credentials SET revoked_at = ? WHERE kind = 'bearer' AND device_id = ? AND revoked_at IS NULL",
                (now, device_id),
            )
            await conn.commit()
            return cursor.rowcount


class IdentityResolver:
    def __init__(self, store: Optional[CredentialStore] = None):
        self.store = store or SQLiteCredentialStore()

    async def resolve(self, request: Request) -> AuthenticatedPrincipal:
        authorization = request.headers.get("authorization", "")
        scheme, separator, token = authorization.partition(" ")
        digest = _token_hash(token) if separator and scheme.lower() == "bearer" and token.startswith(_BEARER_PREFIX) else None
        if digest is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication credential.", headers={"WWW-Authenticate": "Bearer"})
        record = await self.store.resolve_bearer(digest)
        if record is None or record.kind != "bearer" or record.revoked_at is not None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid authentication credential.", headers={"WWW-Authenticate": "Bearer"})
        return AuthenticatedPrincipal(
            user=UserIdentity(user_id=record.user_id),
            device=DeviceIdentity(device_id=record.device_id, user_id=record.user_id, created_at=record.created_at, last_active_at=datetime.now(timezone.utc)),
        )


_default_identity_resolver: Optional[IdentityResolver] = None
_default_credential_store: Optional[SQLiteCredentialStore] = None


def get_credential_store_dependency() -> SQLiteCredentialStore:
    global _default_credential_store
    if _default_credential_store is None:
        _default_credential_store = SQLiteCredentialStore()
    return _default_credential_store


def get_identity_resolver_dependency() -> IdentityResolver:
    global _default_identity_resolver
    if _default_identity_resolver is None:
        _default_identity_resolver = IdentityResolver(get_credential_store_dependency())
    return _default_identity_resolver


async def get_authenticated_principal(
    request: Request,
    resolver: IdentityResolver = Depends(get_identity_resolver_dependency),
) -> AuthenticatedPrincipal:
    resolved = await resolver.resolve(request)
    if "x-user-id" in request.headers:
        raise HTTPException(status_code=400, detail="X-User-ID is not supported for authenticated mutation identity.")
    if "user_id" in request.query_params:
        raise HTTPException(status_code=400, detail="user_id is not supported as an identity selector.")
    return resolved


async def provision_credential(secret: str, store: Optional[SQLiteCredentialStore] = None) -> ProvisionedCredential:
    digest = _token_hash(secret)
    if not secret.startswith(_PROVISIONING_PREFIX) or digest is None:
        raise HTTPException(status_code=401, detail="Missing or invalid provisioning credential.", headers={"WWW-Authenticate": "Bearer"})
    result = await (store or SQLiteCredentialStore()).redeem_provisioning(digest)
    if result is None:
        raise HTTPException(status_code=401, detail="Missing or invalid provisioning credential.", headers={"WWW-Authenticate": "Bearer"})
    return result
