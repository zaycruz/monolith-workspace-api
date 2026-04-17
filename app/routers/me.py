"""GET /api/workspace/me — identity probe. Mirrors the contract."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app import db
from app.auth import AuthContext, get_auth_context
from app.models import AgentIdentity
from app.services.membership import ensure_workspace_for_tenant

router = APIRouter(prefix="/api/workspace", tags=["Identity"])


@router.get("/me", response_model=AgentIdentity)
async def me(auth: AuthContext = Depends(get_auth_context)) -> AgentIdentity:
    if auth.is_agent:
        return AgentIdentity(
            agent_id=auth.identity.agent_container_id,  # type: ignore[union-attr]
            workspace_id=auth.identity.workspace_id,  # type: ignore[union-attr]
            display_name=f"agent:{auth.identity.agent_container_id[:8]}",  # type: ignore[union-attr]
            kind="agent",
        )

    # Human path — fetch or create a display-name record and ensure there's a
    # workspace for their tenant so downstream calls have somewhere to target.
    workspace_id = await ensure_workspace_for_tenant(auth.tenant_id)
    row = await db.fetchrow(
        "SELECT display_name FROM human_identities WHERE clerk_user_id = $1",
        auth.identity.clerk_user_id,  # type: ignore[union-attr]
    )
    if row is None:
        await db.execute(
            """
            INSERT INTO human_identities (id, tenant_id, clerk_user_id, display_name, created_at)
            VALUES ($1, $2, $3, $4, $5)
            """,
            db.new_uuid(),
            auth.tenant_id,
            auth.identity.clerk_user_id,  # type: ignore[union-attr]
            auth.identity.display_name,  # type: ignore[union-attr]
            db.now_iso(),
        )
        display_name = auth.identity.display_name  # type: ignore[union-attr]
    else:
        display_name = row["display_name"]

    return AgentIdentity(
        agent_id=auth.identity.clerk_user_id,  # type: ignore[union-attr]
        workspace_id=workspace_id,
        display_name=display_name,
        kind="user",
    )
