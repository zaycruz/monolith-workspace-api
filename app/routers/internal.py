from __future__ import annotations

import hashlib
import logging
import secrets as secrets_mod
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from app import db
from app.auth import AuthContext, require_service
from app.db import execute, fetchrow
from app.models import BaseModel

logger = logging.getLogger("workspace.internal")

router = APIRouter(prefix="/internal", tags=["Internal"])

_INTERNAL_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _resolve_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        return uuid.uuid5(_INTERNAL_NAMESPACE, value)


class MintMachineTokenRequest(BaseModel):
    agent_container_id: str = Field(..., min_length=1, max_length=255)
    tenant_id: str = Field(..., min_length=1, max_length=255)
    workspace_id: str | None = Field(default=None, min_length=1, max_length=255)


class MintMachineTokenResponse(BaseModel):
    token: str
    token_hash: str


@router.post("/machine-tokens", response_model=MintMachineTokenResponse)
async def mint_machine_token(
    body: MintMachineTokenRequest,
    auth: AuthContext = Depends(require_service),
):
    if auth.tenant_id != body.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={"error": "Tenant mismatch: cannot mint tokens for another tenant"},
        )
    tenant_uuid = _resolve_uuid(body.tenant_id)
    agent_container_uuid = _resolve_uuid(body.agent_container_id)

    if body.workspace_id:
        workspace_uuid = _resolve_uuid(body.workspace_id)
    else:
        workspace_uuid = uuid.uuid5(
            _INTERNAL_NAMESPACE, f"workspace:{body.tenant_id}:default"
        )

    existing_ws = await fetchrow(
        "SELECT id FROM workspaces WHERE id = $1",
        str(workspace_uuid),
    )
    if not existing_ws:
        await execute(
            "INSERT INTO workspaces (id, tenant_id, name, created_at) "
            "VALUES ($1, $2, $3, $4)",
            str(workspace_uuid),
            str(tenant_uuid),
            "default",
            db.now_iso(),
        )
        logger.info(
            "Auto-created workspace %s for tenant %s", workspace_uuid, tenant_uuid
        )

    raw_token = "sk_machine_" + secrets_mod.token_hex(32)
    token_hash = _sha256_hex(raw_token)

    await execute(
        "INSERT INTO agent_machine_tokens "
        "(id, token_hash, agent_container_id, workspace_id, tenant_id, created_at) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        str(uuid.uuid4()),
        token_hash,
        str(agent_container_uuid),
        str(workspace_uuid),
        str(tenant_uuid),
        db.now_iso(),
    )
    logger.info(
        "Minted machine token for agent %s workspace %s tenant %s",
        agent_container_uuid,
        workspace_uuid,
        tenant_uuid,
    )

    return MintMachineTokenResponse(token=raw_token, token_hash=token_hash)
