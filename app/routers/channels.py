"""Channels + members. The contract ships `GET /api/workspace/channels`;
we also expose create/add-member endpoints to bootstrap the workspace.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import AuthContext, get_auth_context
from app.models import (
    AddMemberRequest,
    ChannelListResponse,
    CreateChannelRequest,
)
from app.services.membership import (
    add_member,
    create_channel,
    ensure_workspace_for_tenant,
    list_channels_for_actor,
)

logger = logging.getLogger("workspace.routers.channels")
router = APIRouter(prefix="/api/workspace", tags=["Channels"])


@router.get("/channels", response_model=ChannelListResponse)
async def list_channels(
    agent_id: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
) -> ChannelListResponse:
    # The contract requires `agent_id` when called by a bridge. For the human
    # path we fall back to the auth context's actor.
    if agent_id and auth.is_agent and auth.identity.agent_container_id != agent_id:  # type: ignore[union-attr]
        raise HTTPException(
            status_code=403,
            detail={"error": "agent_id does not match bearer token"},
        )
    workspace_id = await ensure_workspace_for_tenant(auth.tenant_id)
    channels = await list_channels_for_actor(auth, workspace_id)
    return ChannelListResponse(channels=channels)  # type: ignore[arg-type]


@router.post("/channels", status_code=201)
async def create_channel_endpoint(
    body: CreateChannelRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> dict:
    workspace_id = await ensure_workspace_for_tenant(auth.tenant_id)
    return await create_channel(workspace_id, body.name, body.topic, body.kind)


@router.post("/channels/{channel_id}/members", status_code=201)
async def add_channel_member(
    channel_id: str,
    body: AddMemberRequest,
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001 — auth required
) -> dict:
    return await add_member(channel_id, body.human_id, body.agent_container_id)
