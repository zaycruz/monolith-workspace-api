"""SSE stream assembly — consumes the event bus, emits `text/event-stream` frames.

Matches the Fleet API stream.py style: StreamingResponse of an async generator,
yielding `data: {json}\\n\\n` frames plus `id:` for Last-Event-ID resume.

A heartbeat comment (`: heartbeat\\n\\n`) is emitted every SSE_HEARTBEAT_SECONDS
to keep proxies from terminating idle streams (contract: 15s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

from app.config import get_settings
from app.event_bus import subscribe, unsubscribe

logger = logging.getLogger("workspace.realtime")


def _sse_frame(event: dict, event_id: str | None = None) -> str:
    parts = []
    if event_id:
        parts.append(f"id: {event_id}")
    parts.append(f"data: {json.dumps(event, default=str)}")
    return "\n".join(parts) + "\n\n"


async def stream_topics(topics: list[str]) -> AsyncIterator[str]:
    queue, subscribed = await subscribe(topics)
    settings = get_settings()
    last_heartbeat = time.monotonic()
    try:
        while True:
            timeout = max(
                0.5,
                settings.sse_heartbeat_seconds - (time.monotonic() - last_heartbeat),
            )
            try:
                event = await asyncio.wait_for(queue.get(), timeout=timeout)
            except TimeoutError:
                yield ": heartbeat\n\n"
                last_heartbeat = time.monotonic()
                continue
            # Build a stable event id for Last-Event-ID resume (contract hint).
            evt_id = str(event.get("message_id") or event.get("call_id") or time.time_ns())
            yield _sse_frame(event, event_id=evt_id)
    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled for topics=%s", subscribed)
        raise
    finally:
        await unsubscribe(queue, subscribed)
