"""Dual-mode bearer auth.

Two kinds of callers reach this service:

1. **Agents** present an `sk_machine_<64hex>` token (Stripe-style) minted by
   Fleet API when the container was provisioned. We sha256-hash the raw token
   and look it up in `agent_machine_tokens`. Matches the contract's stated
   token format: `sk_machine_[0-9a-f]{64}`.
2. **Humans** present a Clerk-issued JWT. For MVP the service has a
   `VERIFY_CLERK=false` switch that accepts a base64 JWT envelope and extracts
   the `sub` without verifying the signature. Production must flip this on and
   supply `CLERK_JWKS_URL` + `CLERK_ISSUER` for full JWKS verification.

The dependency used by routers is `get_auth_context()`. It raises `401` on a
missing/invalid token and returns an `AuthContext` tagged with the identity
kind (human vs agent).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings
from app.db import fetchrow

logger = logging.getLogger("workspace.auth")

_bearer = HTTPBearer(auto_error=False)
_MACHINE_TOKEN_RE = re.compile(r"^sk_machine_[0-9a-f]{64}$")
_SERVICE_TOKEN_RE = re.compile(r"^sk_service_[0-9a-f]{64}$")


@dataclass
class HumanIdentity:
    kind: Literal["human"] = "human"
    clerk_user_id: str = ""
    display_name: str = ""


@dataclass
class AgentIdentityCtx:
    kind: Literal["agent"] = "agent"
    agent_container_id: str = ""
    workspace_id: str = ""


@dataclass
class ServiceIdentity:
    kind: Literal["service"] = "service"
    service_name: str = ""


@dataclass
class AuthContext:
    tenant_id: str
    identity: HumanIdentity | AgentIdentityCtx | ServiceIdentity
    token_prefix: str  # first 8 of sha256(token) for audit

    @property
    def is_agent(self) -> bool:
        return isinstance(self.identity, AgentIdentityCtx)

    @property
    def is_human(self) -> bool:
        return isinstance(self.identity, HumanIdentity)

    @property
    def is_service(self) -> bool:
        return isinstance(self.identity, ServiceIdentity)

    @property
    def actor_id(self) -> str:
        """Single-column identifier for the caller — agent_container_id, clerk_user_id, or service_name."""
        if isinstance(self.identity, AgentIdentityCtx):
            return self.identity.agent_container_id
        if isinstance(self.identity, ServiceIdentity):
            return self.identity.service_name
        return self.identity.clerk_user_id


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _is_machine_token(token: str) -> bool:
    return bool(_MACHINE_TOKEN_RE.match(token))


def _is_service_token(token: str) -> bool:
    return bool(_SERVICE_TOKEN_RE.match(token))


async def _resolve_service_token(raw: str, request: Request) -> AuthContext:
    settings = get_settings()
    if not settings.workspace_service_token:
        raise HTTPException(
            status_code=401, detail={"error": "Invalid service token"}
        )
    token_hash = _sha256_hex(raw)
    expected_hash = _sha256_hex(settings.workspace_service_token)
    if not hmac.compare_digest(token_hash, expected_hash):
        raise HTTPException(
            status_code=401, detail={"error": "Invalid service token"}
        )
    tenant_id = request.headers.get("x-tenant-id", "")
    if not tenant_id:
        raise HTTPException(
            status_code=401, detail={"error": "Missing X-Tenant-Id header"}
        )
    return AuthContext(
        tenant_id=tenant_id,
        identity=ServiceIdentity(service_name="fleet-api"),
        token_prefix=token_hash[:8],
    )


async def _resolve_machine_token(raw: str) -> AuthContext:
    token_hash = _sha256_hex(raw)
    row = await fetchrow(
        "SELECT agent_container_id, workspace_id, tenant_id FROM agent_machine_tokens "
        "WHERE token_hash = $1 AND revoked_at IS NULL",
        token_hash,
    )
    if not row:
        raise HTTPException(
            status_code=401, detail={"error": "Invalid machine token"}
        )
    return AuthContext(
        tenant_id=str(row["tenant_id"]),
        identity=AgentIdentityCtx(
            agent_container_id=str(row["agent_container_id"]),
            workspace_id=str(row["workspace_id"]),
        ),
        token_prefix=token_hash[:8],
    )


def _decode_unverified_jwt(token: str) -> dict:
    """Decode a JWT without signature verification — DEV ONLY.

    Used when `VERIFY_CLERK=false`. Returns the payload dict, or raises
    `HTTPException(401)` if the envelope is malformed.
    """
    try:
        _, payload_b64, _ = token.split(".")
        # Pad base64 correctly
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        raw = base64.urlsafe_b64decode(padded.encode())
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=401, detail={"error": "Malformed JWT"}
        ) from exc


async def _resolve_clerk_token(raw: str) -> AuthContext:
    settings = get_settings()

    if settings.verify_clerk:
        # TODO(contract-gap): full JWKS verification. Fetch CLERK_JWKS_URL,
        # cache keys, verify signature + iss + exp with PyJWT. Not needed for
        # MVP per scaffold brief.
        raise HTTPException(
            status_code=501,
            detail={"error": "Production Clerk JWKS verification not wired yet"},
        )

    payload = _decode_unverified_jwt(raw)
    clerk_user_id = payload.get("sub") or payload.get("user_id") or ""
    if not clerk_user_id:
        raise HTTPException(
            status_code=401, detail={"error": "JWT missing sub claim"}
        )

    # tenant_id comes from a Clerk custom claim. For MVP, the bridge supplies
    # `X-Tenant-Id` or `org_id`; fall back to `"default"` when both are absent
    # so dev harnesses can use throwaway tokens.
    tenant_id = (
        payload.get("org_id")
        or payload.get("tenant_id")
        or "00000000-0000-0000-0000-000000000000"
    )
    display_name = (
        payload.get("name")
        or payload.get("email")
        or payload.get("preferred_username")
        or clerk_user_id
    )

    return AuthContext(
        tenant_id=str(tenant_id),
        identity=HumanIdentity(
            clerk_user_id=clerk_user_id,
            display_name=str(display_name),
        ),
        token_prefix=_sha256_hex(raw)[:8],
    )


async def get_auth_context(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthContext:
    settings = get_settings()

    if not settings.auth_enabled:
        # Dev bypass — still tag the actor so routers have something to key on.
        return AuthContext(
            tenant_id="00000000-0000-0000-0000-000000000000",
            identity=HumanIdentity(
                clerk_user_id="dev",
                display_name="dev",
            ),
            token_prefix="devbypass",
        )

    if not credentials:
        raise HTTPException(
            status_code=401, detail={"error": "Missing Authorization header"}
        )

    token = credentials.credentials.strip()
    if _is_machine_token(token):
        return await _resolve_machine_token(token)
    if _is_service_token(token):
        return await _resolve_service_token(token, request)
    return await _resolve_clerk_token(token)


async def require_agent(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not auth.is_agent:
        raise HTTPException(
            status_code=403, detail={"error": "Agent token required"}
        )
    return auth


async def require_service(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if not auth.is_service:
        raise HTTPException(
            status_code=403, detail={"error": "Service token required"}
        )
    return auth
