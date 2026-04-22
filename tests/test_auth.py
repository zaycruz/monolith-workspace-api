"""Auth behaviors: missing bearer → 401, invalid machine token → 401,
agent path vs human path resolved correctly."""

from __future__ import annotations


def test_missing_bearer_rejected(client):
    r = client.get("/api/workspace/me")
    assert r.status_code == 401
    assert r.json()["error"] == "Missing Authorization header"


def test_invalid_machine_token_rejected(client):
    r = client.get(
        "/api/workspace/me",
        headers={"Authorization": "Bearer sk_machine_" + "0" * 64},
    )
    assert r.status_code == 401
    assert "Invalid" in r.json()["error"]


def test_malformed_jwt_rejected(client):
    r = client.get(
        "/api/workspace/me",
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert r.status_code == 401


def test_agent_me_identity(client, agent_a_headers):
    r = client.get("/api/workspace/me", headers=agent_a_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "agent"
    assert data["workspace_id"]
    assert data["agent_id"]


def test_human_me_identity(client, human_headers):
    r = client.get("/api/workspace/me", headers=human_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "user"
    assert data["display_name"].startswith("display-")


def test_invalid_service_token_rejected(client):
    r = client.get(
        "/api/workspace/dms/some-counterpart",
        headers={"Authorization": "Bearer sk_service_" + "0" * 64, "X-Tenant-Id": "11111111-1111-1111-1111-111111111111"},
    )
    assert r.status_code == 401
    assert "Invalid" in r.json()["error"]


def test_service_token_missing_tenant_rejected(client, service_headers):
    headers = {k: v for k, v in service_headers.items() if k.lower() != "x-tenant-id"}
    r = client.get("/api/workspace/dms/some-counterpart", headers=headers)
    assert r.status_code == 401
    assert "Missing X-Tenant-Id" in r.json()["error"]


def test_service_dm_resolve(client, service_headers):
    r = client.get("/api/workspace/dms/some-counterpart", headers=service_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["channel_id"]
    assert data["workspace_id"]
