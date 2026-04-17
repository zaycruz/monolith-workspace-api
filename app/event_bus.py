"""In-process asyncio pub/sub for SSE fanout.

Topics are string keys like `channel:<uuid>` or `agent:<uuid>`. Subscribers
get an asyncio.Queue they drain from. One queue per SSE connection — events
are fanned out by the publisher pushing onto every matching queue.

TODO(multi-replica): Replace with Redis pub/sub (or NATS) when we deploy
more than one workspace-api instance. This module's API (subscribe/publish)
stays the same; only the transport changes.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

logger = logging.getLogger("workspace.event_bus")

# topic -> set of Queues
_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
_lock = asyncio.Lock()


async def subscribe(topics: list[str]) -> tuple[asyncio.Queue, list[str]]:
    """Create a queue subscribed to all given topics. Returns (queue, topics)."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    async with _lock:
        for t in topics:
            _subscribers[t].add(queue)
    return queue, topics


async def unsubscribe(queue: asyncio.Queue, topics: list[str]) -> None:
    async with _lock:
        for t in topics:
            subs = _subscribers.get(t)
            if subs and queue in subs:
                subs.discard(queue)
                if not subs:
                    _subscribers.pop(t, None)


async def publish(topic: str, event: dict[str, Any]) -> int:
    """Fan out `event` to every queue subscribed to `topic`.

    Returns the number of queues the event was delivered to. A queue that is
    at capacity is dropped from the subscribers list (slow consumers must
    reconnect); this matches the contract's expectation that bridges reconnect
    on disconnect.
    """
    async with _lock:
        queues = list(_subscribers.get(topic, ()))
    delivered = 0
    for q in queues:
        try:
            q.put_nowait(event)
            delivered += 1
        except asyncio.QueueFull:
            logger.warning("Dropping slow subscriber on topic %s", topic)
            async with _lock:
                _subscribers.get(topic, set()).discard(q)
    return delivered


def channel_topic(channel_id: str) -> str:
    return f"channel:{channel_id}"


def agent_topic(agent_id: str) -> str:
    return f"agent:{agent_id}"


# ── Tool-call rendezvous ────────────────────────────────────────────────────
#
# POST /api/workspace/agents/{id}/tool-call needs to block until the target
# bridge returns a result via POST .../tool-calls/{call_id}/result. We model
# that with a per-call-id Future.

_pending_tool_calls: dict[str, asyncio.Future] = {}


def create_tool_call_future(call_id: str) -> asyncio.Future:
    fut: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_tool_calls[call_id] = fut
    return fut


def resolve_tool_call(call_id: str, result: Any | None, error: str | None) -> bool:
    """Returns True if a waiter was found and resolved."""
    fut = _pending_tool_calls.pop(call_id, None)
    if fut is None or fut.done():
        return False
    fut.set_result({"result": result, "error": error})
    return True


def cancel_tool_call(call_id: str) -> None:
    _pending_tool_calls.pop(call_id, None)
