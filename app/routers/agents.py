"""Agent-to-agent tool call routing.

POST /api/workspace/agents/{target_agent_id}/tool-call
    - caller (any authenticated actor) posts a tool invocation
    - backend emits a `tool_call.invoked` SSE event to the target's agent
      topic (`agent:<uuid>`)
    - backend blocks on a per-call-id Future
    - target bridge POSTs to /api/workspace/tool-calls/{call_id}/result
    - backend resolves the future and returns the result to the caller
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthContext, get_auth_context
from app.event_bus import (
    agent_topic,
    cancel_tool_call,
    create_tool_call_future,
    publish,
    resolve_tool_call,
)
from app.models import (
    CollabToolCallRequest,
    CollabToolCallResponse,
    OkResponse,
    ToolCallResultRequest,
)

logger = logging.getLogger("workspace.routers.agents")
router = APIRouter(prefix="/api/workspace", tags=["Agents"])

TOOL_CALL_TIMEOUT_SECONDS = 60


@router.post(
    "/agents/{target_agent_id}/tool-call",
    response_model=CollabToolCallResponse,
)
async def invoke_tool(
    target_agent_id: str,
    body: CollabToolCallRequest,
    auth: AuthContext = Depends(get_auth_context),
) -> CollabToolCallResponse:
    # Trust the bearer for tenant isolation. The caller_agent_id in the body
    # is informational — we prefer the auth context for routing.
    caller_id = (
        auth.identity.agent_container_id  # type: ignore[union-attr]
        if auth.is_agent
        else body.caller_agent_id
    )

    call_id = str(uuid.uuid4())
    fut = create_tool_call_future(call_id)

    delivered = await publish(
        agent_topic(target_agent_id),
        {
            "type": "tool_call.invoked",
            "target_agent_id": target_agent_id,
            "caller_agent_id": caller_id,
            "tool_name": body.tool_name,
            "args": body.tool_args,
            "call_id": call_id,
        },
    )
    if delivered == 0:
        cancel_tool_call(call_id)
        raise HTTPException(
            status_code=404,
            detail={"error": f"Target agent {target_agent_id} is not connected"},
        )

    try:
        result = await asyncio.wait_for(fut, timeout=TOOL_CALL_TIMEOUT_SECONDS)
    except TimeoutError:
        cancel_tool_call(call_id)
        return CollabToolCallResponse(error="tool_call_timeout")
    return CollabToolCallResponse(
        result=result.get("result"), error=result.get("error")
    )


@router.post(
    "/tool-calls/{call_id}/result",
    response_model=OkResponse,
)
async def report_tool_call_result(
    call_id: str,
    body: ToolCallResultRequest,
    auth: AuthContext = Depends(get_auth_context),  # noqa: ARG001
) -> OkResponse:
    resolved = resolve_tool_call(call_id, body.result, body.error)
    if not resolved:
        raise HTTPException(
            status_code=404, detail={"error": f"No pending call with id {call_id}"}
        )
    return OkResponse(ok=True)
