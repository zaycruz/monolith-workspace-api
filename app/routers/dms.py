"""Direct messages — 1:1 threads modeled as synthetic channels.

TODO(contract-gap): The openapi spec doesn't define DM endpoints (only the
shared channel surface with `kind: 'dm'`). We expose this as a convenience
for bridges that want a symmetric "talk to counterpart" call. A DM
materializes a hidden channel behind the scenes with exactly two members.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app import db
from app.auth import AuthContext, get_auth_context
from app.models import SendMessageRequest, SendMessageResponse
from app.services.membership import (
    add_member,
    create_channel,
    ensure_workspace_for_tenant,
)
from app.services.messaging import read_channel, send_message

logger = logging.getLogger("workspace.routers.dms")
router = APIRouter(prefix="/api/workspace", tags=["DMs"])


async def _resolve_dm_channel(auth: AuthContext, counterpart_id: str) -> dict[str, str]:
    workspace_id = await ensure_workspace_for_tenant(auth.tenant_id)
    actor_id = auth.actor_id
    # Deterministic channel name for the pair — sort so either side resolves
    # to the same channel.
    pair_key = "|".join(sorted([actor_id, counterpart_id]))
    channel_name = f"dm:{pair_key[:60]}"

    row = await db.fetchrow(
        "SELECT id FROM channels WHERE workspace_id = $1 AND name = $2",
        workspace_id,
        channel_name,
    )
    if row:
        return {"channel_id": str(row["id"]), "workspace_id": workspace_id}

    created = await create_channel(workspace_id, channel_name, None, kind="dm")
    channel_id = created["channel_id"]
    # Add both participants. We don't know whether the counterpart is a human
    # or an agent at this layer — assume agent UUID format, human otherwise.
    # TODO(contract-gap): pass counterpart kind explicitly.
    # Service auth is not a channel member; skip adding the service itself.
    if not auth.is_service:
        human_id_actor = auth.identity.clerk_user_id if auth.is_human else None  # type: ignore[union-attr]
        agent_id_actor = auth.identity.agent_container_id if auth.is_agent else None  # type: ignore[union-attr]
        await add_member(channel_id, human_id_actor, agent_id_actor)
    # Counterpart — best effort, assume agent if it looks like a UUID.
    is_uuid = len(counterpart_id) >= 32 and "-" in counterpart_id
    await add_member(
        channel_id,
        human_id=None if is_uuid else counterpart_id,
        agent_container_id=counterpart_id if is_uuid else None,
    )
    return {"channel_id": channel_id, "workspace_id": workspace_id}


@router.post(
    "/dms/{counterpart_id}/messages",
    response_model=SendMessageResponse,
)
async def send_dm(
    counterpart_id: str,
    body: SendMessageRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> SendMessageResponse:
    resolved = await _resolve_dm_channel(auth, counterpart_id)
    channel_id = resolved["channel_id"]
    result = await send_message(
        auth=auth,
        channel_id=channel_id,
        content=body.content,
        idempotency_key=body.idempotency_key,
        mentions=body.mentions or [counterpart_id],
        thread_parent_id=body.thread_parent_id,
        tool_calls=body.tool_calls,
    )
    return SendMessageResponse(
        message_id=result.message_id, timestamp=result.timestamp
    )


@router.get("/dms/{counterpart_id}/messages")
async def read_dm(
    counterpart_id: str,
    cursor: str | None = None,
    limit: int = 50,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    resolved = await _resolve_dm_channel(auth, counterpart_id)
    channel_id = resolved["channel_id"]
    if not channel_id:
        raise HTTPException(404, detail={"error": "DM channel not found"})
    return await read_channel(channel_id, cursor, limit)


@router.get("/dms/{counterpart_id}")
async def get_dm(
    counterpart_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    return await _resolve_dm_channel(auth, counterpart_id)
