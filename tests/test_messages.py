"""Message send, idempotency dedup, and cursor-based read."""

from __future__ import annotations

import uuid


def _make_channel(client, headers) -> str:
    r = client.post(
        "/api/workspace/channels", headers=headers, json={"name": f"c-{uuid.uuid4().hex[:8]}"}
    )
    assert r.status_code == 201
    return r.json()["channel_id"]


def test_send_message_201_creates_row(client, agent_a_headers):
    channel_id = _make_channel(client, agent_a_headers)
    r = client.post(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=agent_a_headers,
        json={"content": "hello", "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["message_id"]
    assert body["timestamp"]


def test_send_message_idempotency_dedup(client, agent_a_headers):
    channel_id = _make_channel(client, agent_a_headers)
    key = str(uuid.uuid4())

    r1 = client.post(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=agent_a_headers,
        json={"content": "first", "idempotency_key": key},
    )
    r2 = client.post(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=agent_a_headers,
        json={"content": "second", "idempotency_key": key},
    )
    assert r1.status_code == 201
    assert r2.status_code == 200  # dedup
    assert r1.json()["message_id"] == r2.json()["message_id"]


def test_read_channel_pagination(client, agent_a_headers):
    channel_id = _make_channel(client, agent_a_headers)
    for i in range(5):
        client.post(
            f"/api/workspace/channels/{channel_id}/messages",
            headers=agent_a_headers,
            json={"content": f"msg-{i}", "idempotency_key": str(uuid.uuid4())},
        )

    r = client.get(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=agent_a_headers,
        params={"limit": 2},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["messages"]) == 2
    assert body["next_cursor"] is not None

    r2 = client.get(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=agent_a_headers,
        params={"limit": 10, "cursor": body["next_cursor"]},
    )
    assert r2.status_code == 200
    assert len(r2.json()["messages"]) == 3
