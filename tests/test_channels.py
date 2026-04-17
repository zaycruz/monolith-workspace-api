"""Create channel, add member (human + agent), list channels."""

from __future__ import annotations

from tests.conftest import AGENT_ID_A


def test_create_and_list_channel(client, agent_a_headers):
    r = client.post(
        "/api/workspace/channels",
        headers=agent_a_headers,
        json={"name": "eng-alpha", "topic": "engineering"},
    )
    assert r.status_code == 201, r.text
    channel_id = r.json()["channel_id"]

    # Before joining, the agent is not a member of any channel
    r = client.get(
        "/api/workspace/channels",
        headers=agent_a_headers,
        params={"agent_id": AGENT_ID_A},
    )
    assert r.status_code == 200
    assert r.json()["channels"] == []

    # Join
    r = client.post(
        f"/api/workspace/channels/{channel_id}/members",
        headers=agent_a_headers,
        json={"agent_container_id": AGENT_ID_A},
    )
    assert r.status_code == 201

    # Now it shows up
    r = client.get(
        "/api/workspace/channels",
        headers=agent_a_headers,
        params={"agent_id": AGENT_ID_A},
    )
    assert r.status_code == 200
    channels = r.json()["channels"]
    assert len(channels) == 1
    assert channels[0]["name"] == "eng-alpha"


def test_add_member_requires_exactly_one_kind(client, agent_a_headers):
    r = client.post(
        "/api/workspace/channels",
        headers=agent_a_headers,
        json={"name": "tmp"},
    )
    channel_id = r.json()["channel_id"]
    r = client.post(
        f"/api/workspace/channels/{channel_id}/members",
        headers=agent_a_headers,
        json={"human_id": "u1", "agent_container_id": AGENT_ID_A},
    )
    assert r.status_code == 400
