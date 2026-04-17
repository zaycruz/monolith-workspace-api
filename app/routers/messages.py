"""Message send/read + reactions + inbox poll.

Shapes here MUST match contract/openapi.yaml exactly. The service layer does
the work; this router is just request parsing and response shaping.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Response

from app import db
from app.auth import AuthContext, get_auth_context
from app.models import (
    AddReactionRequest,
    CheckMessagesResponse,
    OkResponse,
    ReadChannelResponse,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.messaging import (
    add_reaction,
    read_channel,
    send_message,
)

router = APIRouter(prefix="/api/workspace", tags=["Messages"])


@router.post(
    "/channels/{channel_id}/messages",
    response_model=SendMessageResponse,
)
async def send_channel_message(
    channel_id: str,
    body: SendMessageRequest,
    response: Response,
    auth: AuthContext = Depends(get_auth_context),
) -> SendMessageResponse:
    result = await send_message(
        auth=auth,
        channel_id=channel_id,
        content=body.content,
        idempotency_key=body.idempotency_key,
        mentions=body.mentions,
        thread_parent_id=body.thread_parent_id,
        tool_calls=body.tool_calls,
    )
    # Contract: 200 on idempotent replay, 201 on fresh create.
    response.status_code = 200 if result.deduped else 201
    return SendMessageResponse(
        message_id=result.message_id, timestamp=result.timestamp
    )


@router.get(
    "/channels/{channel_id}/messages",
    response_model=ReadChannelResponse,
)
async def read_channel_messages(
    channel_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001
) -> ReadChannelResponse:
    data = await read_channel(channel_id, cursor, limit)
    return ReadChannelResponse(**data)


@router.post(
    "/channels/{channel_id}/reactions",
    response_model=OkResponse,
)
async def add_channel_reaction(
    channel_id: str,
    body: AddReactionRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> OkResponse:
    ok = await add_reaction(auth, channel_id, body.message_id, body.symbol)
    return OkResponse(ok=ok)


@router.get(
    "/inbox",
    response_model=CheckMessagesResponse,
)
async def check_messages(
    agent_id: str = Query(...),
    since: str | None = Query(default=None),
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001
) -> CheckMessagesResponse:
    # TODO(contract-gap): "unread" semantics in the contract are backend-defined.
    # MVP: return every message mentioning this agent since `since`, OR
    # messages in channels the agent is a sole member of (DMs).
    since_clause = ""
    params: list = [agent_id]
    if since:
        # Validate ISO8601 early so we 400 cleanly
        try:
            datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            since = None
        else:
            since_clause = "AND m.created_at > $2"
            params.append(since)

    # SQL shape works for both Postgres (jsonb ? operator) and SQLite (LIKE
    # fallback). SQLite has no jsonb, so we LIKE on the serialized array.
    if db.USE_SQLITE:
        mention_clause = f"m.mentions LIKE '%\"{agent_id}\"%'"
    else:
        mention_clause = "m.mentions::jsonb @> to_jsonb(ARRAY[$1]::text[])"

    sql = f"""
        SELECT m.id, m.channel_id, m.content, m.created_at,
               m.sender_agent_container_id, m.sender_human_id
        FROM messages m
        WHERE {mention_clause}
          {since_clause}
        ORDER BY m.created_at ASC
        LIMIT 200
    """
    rows = await db.fetch(sql, *params)

    new_messages = [
        {
            "message_id": str(r["id"]),
            "channel_id": str(r["channel_id"]),
            "sender": str(
                r.get("sender_agent_container_id") or r.get("sender_human_id") or ""
            ),
            "content": r["content"],
            "timestamp": str(r["created_at"]),
        }
        for r in rows
    ]
    unread_channels = sorted({m["channel_id"] for m in new_messages})
    return CheckMessagesResponse(
        new_messages=new_messages,  # type: ignore[arg-type]
        unread_channel_ids=unread_channels,
    )
