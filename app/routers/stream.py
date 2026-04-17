"""SSE endpoint — realtime inbound for bridges.

Query shape matches the contract:
    GET /api/workspace/stream?agent_id=&workspace_id=&channels=

`channels` is a comma-separated list of channel UUIDs. We subscribe to each
channel's topic plus the agent's direct topic (for `tool_call.invoked`).

Response headers match both the contract's spec (content-type, cache-control,
x-accel-buffering) and Fleet API's stream.py — byte-for-byte identical.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.auth import AuthContext, get_auth_context
from app.event_bus import agent_topic, channel_topic
from app.services.realtime import stream_topics

logger = logging.getLogger("workspace.routers.stream")
router = APIRouter(prefix="/api/workspace", tags=["Stream"])


@router.get(
    "/stream",
    summary="Workspace realtime SSE stream",
    description=(
        "Server-sent events for inbound workspace traffic. Matches the bridge "
        "contract in packages/chat-bridge/contract/WORKSPACE_API.md."
    ),
)
async def workspace_stream(
    agent_id: str = Query(...),
    workspace_id: str = Query(...),
    channels: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),
):
    # Enforce that agent callers only stream their own agent_id + workspace_id.
    if auth.is_agent:
        if auth.identity.agent_container_id != agent_id:  # type: ignore[union-attr]
            raise HTTPException(
                status_code=403,
                detail={"error": "agent_id does not match bearer token"},
            )
        if auth.identity.workspace_id != workspace_id:  # type: ignore[union-attr]
            raise HTTPException(
                status_code=403,
                detail={"error": "workspace_id does not match bearer token"},
            )

    topics: list[str] = [agent_topic(agent_id)]
    if channels:
        for cid in channels.split(","):
            cid = cid.strip()
            if cid:
                topics.append(channel_topic(cid))

    return StreamingResponse(
        stream_topics(topics),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
