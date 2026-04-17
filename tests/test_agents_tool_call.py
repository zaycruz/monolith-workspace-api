"""Agent A invokes tool on Agent B — target receives tool_call.invoked event
and the caller sees the result after B reports it back.
"""

from __future__ import annotations

import asyncio

import pytest

from app.event_bus import (
    agent_topic,
    cancel_tool_call,
    create_tool_call_future,
    resolve_tool_call,
    subscribe,
    unsubscribe,
)
from tests.conftest import AGENT_ID_B


@pytest.mark.asyncio
async def test_tool_call_rendezvous_happy_path():
    call_id = "call-happy"
    fut = create_tool_call_future(call_id)
    resolved = resolve_tool_call(call_id, {"stdout": "ok"}, None)
    assert resolved is True
    result = await asyncio.wait_for(fut, timeout=1.0)
    assert result == {"result": {"stdout": "ok"}, "error": None}


@pytest.mark.asyncio
async def test_tool_call_cancel():
    call_id = "call-cancel"
    fut = create_tool_call_future(call_id)
    cancel_tool_call(call_id)
    # Cancelled future has no waiter record; resolving returns False
    assert resolve_tool_call(call_id, None, "boom") is False
    # The future remains unresolved — callers should time out themselves
    assert not fut.done()


@pytest.mark.asyncio
async def test_target_receives_tool_call_invoked_event():
    topic = agent_topic(AGENT_ID_B)
    queue, topics = await subscribe([topic])
    try:
        from app.event_bus import publish as pub

        await pub(
            topic,
            {
                "type": "tool_call.invoked",
                "target_agent_id": AGENT_ID_B,
                "caller_agent_id": "agent-a",
                "tool_name": "shell",
                "args": {"command": "ls"},
                "call_id": "c1",
            },
        )
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event["type"] == "tool_call.invoked"
        assert event["target_agent_id"] == AGENT_ID_B
        assert event["call_id"] == "c1"
    finally:
        await unsubscribe(queue, topics)


def test_tool_call_target_not_connected_returns_404(client, agent_a_headers):
    """When no SSE subscriber exists for the target, the HTTP call 404s."""
    r = client.post(
        f"/api/workspace/agents/{AGENT_ID_B}/tool-call",
        headers=agent_a_headers,
        json={
            "tool_name": "shell",
            "tool_args": {"command": "ls"},
            "caller_agent_id": "agent-a",
        },
    )
    assert r.status_code == 404
