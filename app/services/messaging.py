"""Core messaging behaviors: send with idempotency, read with cursors,
reactions with the fixed symbol set.

Every write publishes the corresponding SSE event (`message.created`,
`reaction.added`) before returning.  The cursor is a base64 of the last
message's `created_at|id` — opaque to callers, deterministic on our side.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app import db
from app.auth import AuthContext
from app.event_bus import channel_topic, publish
from app.models import ALLOWED_REACTION_SYMBOLS

logger = logging.getLogger("workspace.messaging")


@dataclass
class SendMessageResult:
    message_id: str
    timestamp: str
    deduped: bool


def _sender_column_for(auth: AuthContext) -> tuple[str, str]:
    """Return (column_name, value) for the message sender columns."""
    if auth.is_agent:
        return "sender_agent_container_id", auth.identity.agent_container_id  # type: ignore[union-attr]
    return "sender_human_id", auth.identity.clerk_user_id  # type: ignore[union-attr]


def _sender_display(row: dict[str, Any]) -> str:
    """Collapse the polymorphic sender columns into the single `sender` wire field."""
    return str(row.get("sender_agent_container_id") or row.get("sender_human_id") or "")


def _encode_cursor(row: dict[str, Any]) -> str:
    raw = f"{row['created_at']}|{row['id']}"
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if not cursor:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        created_at, mid = raw.split("|", 1)
        return created_at, mid
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, detail={"error": "Invalid cursor"}) from exc


async def send_message(
    auth: AuthContext,
    channel_id: str,
    content: str,
    idempotency_key: str,
    mentions: list[str],
    thread_parent_id: str | None,
    tool_calls: list[dict[str, Any]] | None,
) -> SendMessageResult:
    # Idempotency check — identical key in same channel returns original row.
    existing = await db.fetchrow(
        "SELECT id, created_at FROM messages WHERE channel_id = $1 AND idempotency_key = $2",
        channel_id,
        idempotency_key,
    )
    if existing:
        return SendMessageResult(
            message_id=str(existing["id"]),
            timestamp=str(existing["created_at"]),
            deduped=True,
        )

    sender_col, sender_val = _sender_column_for(auth)
    other_col = (
        "sender_human_id"
        if sender_col == "sender_agent_container_id"
        else "sender_agent_container_id"
    )

    message_id = db.new_uuid()
    now = db.now_iso()

    sql = f"""
        INSERT INTO messages (
            id, channel_id, thread_parent_id,
            {sender_col}, {other_col},
            content, tool_calls, mentions, idempotency_key, created_at
        ) VALUES ($1, $2, $3, $4, NULL, $5, $6, $7, $8, $9)
    """
    await db.execute(
        sql,
        message_id,
        channel_id,
        thread_parent_id,
        sender_val,
        content,
        db.dump_json(tool_calls),
        db.dump_json(mentions),
        idempotency_key,
        now,
    )

    await publish(
        channel_topic(channel_id),
        {
            "type": "message.created",
            "channel_id": channel_id,
            "message_id": message_id,
            "sender": sender_val,
            "content": content,
            "timestamp": now,
            "mentions": mentions,
            "thread_parent_id": thread_parent_id,
        },
    )

    return SendMessageResult(message_id=message_id, timestamp=now, deduped=False)


async def read_channel(
    channel_id: str, cursor: str | None, limit: int
) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    decoded = _decode_cursor(cursor)

    if decoded:
        created_at, mid = decoded
        rows = await db.fetch(
            """
            SELECT * FROM messages
            WHERE channel_id = $1
              AND (created_at, id) > ($2, $3)
            ORDER BY created_at ASC, id ASC
            LIMIT $4
            """,
            channel_id,
            created_at,
            mid,
            limit + 1,
        )
    else:
        rows = await db.fetch(
            """
            SELECT * FROM messages
            WHERE channel_id = $1
            ORDER BY created_at ASC, id ASC
            LIMIT $2
            """,
            channel_id,
            limit + 1,
        )

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = _encode_cursor(page[-1]) if has_more and page else None

    messages = [
        {
            "message_id": str(r["id"]),
            "channel_id": str(r["channel_id"]),
            "sender": _sender_display(r),
            "content": r["content"],
            "timestamp": str(r["created_at"]),
            "mentions": db.load_json(r.get("mentions")) or [],
            "thread_parent_id": str(r["thread_parent_id"]) if r.get("thread_parent_id") else None,
        }
        for r in page
    ]
    return {"messages": messages, "next_cursor": next_cursor}


async def add_reaction(
    auth: AuthContext, channel_id: str, message_id: str, symbol: str
) -> bool:
    if symbol not in ALLOWED_REACTION_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": f"Symbol must be one of {sorted(ALLOWED_REACTION_SYMBOLS)}",
            },
        )

    sender_col, sender_val = _sender_column_for(auth)
    other_col = (
        "sender_human_id"
        if sender_col == "sender_agent_container_id"
        else "sender_agent_container_id"
    )

    reaction_id = db.new_uuid()
    try:
        await db.execute(
            f"""
            INSERT INTO reactions (id, message_id, symbol, {sender_col}, {other_col}, created_at)
            VALUES ($1, $2, $3, $4, NULL, $5)
            """,
            reaction_id,
            message_id,
            symbol,
            sender_val,
            db.now_iso(),
        )
    except Exception as exc:
        # Unique violation => already reacted. Treat as success.
        if "UNIQUE" in str(exc).upper() or "duplicate" in str(exc).lower():
            return True
        raise

    await publish(
        channel_topic(channel_id),
        {
            "type": "reaction.added",
            "channel_id": channel_id,
            "message_id": message_id,
            "symbol": symbol,
            "sender": sender_val,
        },
    )
    return True
