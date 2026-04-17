"""Reactions: allowed symbol set, duplicate becomes a no-op."""

from __future__ import annotations

import uuid


def _channel_and_message(client, headers) -> tuple[str, str]:
    r = client.post(
        "/api/workspace/channels", headers=headers, json={"name": f"c-{uuid.uuid4().hex[:8]}"}
    )
    channel_id = r.json()["channel_id"]
    r = client.post(
        f"/api/workspace/channels/{channel_id}/messages",
        headers=headers,
        json={"content": "hi", "idempotency_key": str(uuid.uuid4())},
    )
    return channel_id, r.json()["message_id"]


def test_add_reaction_valid_symbol(client, agent_a_headers):
    channel_id, message_id = _channel_and_message(client, agent_a_headers)
    r = client.post(
        f"/api/workspace/channels/{channel_id}/reactions",
        headers=agent_a_headers,
        json={"message_id": message_id, "symbol": "✓"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_add_reaction_invalid_symbol(client, agent_a_headers):
    channel_id, message_id = _channel_and_message(client, agent_a_headers)
    r = client.post(
        f"/api/workspace/channels/{channel_id}/reactions",
        headers=agent_a_headers,
        json={"message_id": message_id, "symbol": ":thumbsup:"},
    )
    assert r.status_code == 400


def test_duplicate_reaction_is_ok(client, agent_a_headers):
    channel_id, message_id = _channel_and_message(client, agent_a_headers)
    for _ in range(2):
        r = client.post(
            f"/api/workspace/channels/{channel_id}/reactions",
            headers=agent_a_headers,
            json={"message_id": message_id, "symbol": "+"},
        )
        assert r.status_code == 200
