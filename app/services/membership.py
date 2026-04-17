"""Channel membership + workspace creation helpers.

TODO(contract-gap): The contract doesn't define channel-creation or
membership-management endpoints. We surface them here anyway — the bridge
needs *something* to bootstrap. Once the contract is updated, align shapes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app import db
from app.auth import AuthContext
from app.event_bus import channel_topic, publish

logger = logging.getLogger("workspace.membership")


async def ensure_workspace_for_tenant(tenant_id: str, name: str = "default") -> str:
    """Return the workspace id for this tenant, creating if missing."""
    row = await db.fetchrow(
        "SELECT id FROM workspaces WHERE tenant_id = $1 LIMIT 1", tenant_id
    )
    if row:
        return str(row["id"])
    ws_id = db.new_uuid()
    await db.execute(
        "INSERT INTO workspaces (id, tenant_id, name, created_at) VALUES ($1, $2, $3, $4)",
        ws_id,
        tenant_id,
        name,
        db.now_iso(),
    )
    return ws_id


async def create_channel(
    workspace_id: str,
    name: str,
    topic: str | None,
    kind: str = "public",
) -> dict[str, Any]:
    channel_id = db.new_uuid()
    try:
        await db.execute(
            """
            INSERT INTO channels (id, workspace_id, name, topic, kind, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            channel_id,
            workspace_id,
            name,
            topic,
            kind,
            db.now_iso(),
        )
    except Exception as exc:
        if "UNIQUE" in str(exc).upper() or "duplicate" in str(exc).lower():
            raise HTTPException(
                status_code=409,
                detail={"error": f"Channel '{name}' already exists in this workspace"},
            ) from exc
        raise
    return {
        "channel_id": channel_id,
        "name": name,
        "kind": kind,
        "topic": topic,
    }


async def add_member(
    channel_id: str,
    human_id: str | None,
    agent_container_id: str | None,
) -> dict[str, Any]:
    if (human_id is None) == (agent_container_id is None):
        raise HTTPException(
            status_code=400,
            detail={"error": "Exactly one of human_id or agent_container_id is required"},
        )
    member_id = db.new_uuid()
    await db.execute(
        """
        INSERT INTO channel_members (id, channel_id, human_id, agent_container_id, joined_at)
        VALUES ($1, $2, $3, $4, $5)
        """,
        member_id,
        channel_id,
        human_id,
        agent_container_id,
        db.now_iso(),
    )

    await publish(
        channel_topic(channel_id),
        {
            "type": "member.added",
            "channel_id": channel_id,
            "member_type": "user" if human_id else "agent",
            "member_id": human_id or agent_container_id,
        },
    )

    return {
        "id": member_id,
        "channel_id": channel_id,
        "human_id": human_id,
        "agent_container_id": agent_container_id,
    }


async def list_channels_for_actor(
    auth: AuthContext, workspace_id: str
) -> list[dict[str, Any]]:
    """Return the channels this actor is a member of in the given workspace."""
    if auth.is_agent:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.kind
            FROM channels c
            JOIN channel_members m ON m.channel_id = c.id
            WHERE c.workspace_id = $1 AND m.agent_container_id = $2
            """,
            workspace_id,
            auth.identity.agent_container_id,  # type: ignore[union-attr]
        )
    else:
        rows = await db.fetch(
            """
            SELECT c.id, c.name, c.kind
            FROM channels c
            JOIN channel_members m ON m.channel_id = c.id
            WHERE c.workspace_id = $1 AND m.human_id = $2
            """,
            workspace_id,
            auth.identity.clerk_user_id,  # type: ignore[union-attr]
        )
    return [
        {
            "channel_id": str(r["id"]),
            "name": r["name"],
            "kind": r.get("kind", "public"),
        }
        for r in rows
    ]
