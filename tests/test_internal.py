"""Internal service-only endpoints for machine-token minting."""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid

from app import db
from app.routers import internal


def _ensure_sqlite_now_function() -> None:
    # internal router SQL uses NOW(); register compatibility for SQLite tests.
    asyncio.run(db._sqlite_conn.create_function("NOW", 0, db.now_iso))


def _service_headers_for(service_headers: dict[str, str], tenant_id: str) -> dict[str, str]:
    return {**service_headers, "X-Tenant-Id": tenant_id}


def test_mint_machine_token_requires_service_auth(client):
    payload = {
        "agent_container_id": "agent-no-auth",
        "tenant_id": "tenant-no-auth",
    }

    r = client.post("/internal/machine-tokens", json=payload)

    assert r.status_code == 401
    assert r.json()["error"] == "Missing Authorization header"


def test_mint_machine_token_returns_machine_token_shape(client, service_headers):
    _ensure_sqlite_now_function()
    payload = {
        "agent_container_id": "agent-shape",
        "tenant_id": "tenant-shape",
    }

    r = client.post(
        "/internal/machine-tokens",
        json=payload,
        headers=_service_headers_for(service_headers, payload["tenant_id"]),
    )

    assert r.status_code == 200
    data = r.json()
    assert re.fullmatch(r"sk_machine_[0-9a-f]{64}", data["token"])
    assert data["token_hash"] == hashlib.sha256(data["token"].encode()).hexdigest()
    assert data["workspace_id"] == str(
        uuid.uuid5(internal._INTERNAL_NAMESPACE, f"workspace:{payload['tenant_id']}:default")
    )
    assert data["agent_container_id"] == str(
        uuid.uuid5(internal._INTERNAL_NAMESPACE, payload["agent_container_id"])
    )


def test_mint_machine_token_auto_creates_workspace(client, service_headers):
    _ensure_sqlite_now_function()
    tenant_id = "tenant-auto-create"
    payload = {
        "agent_container_id": "agent-auto-create",
        "tenant_id": tenant_id,
    }

    r = client.post(
        "/internal/machine-tokens",
        json=payload,
        headers=_service_headers_for(service_headers, tenant_id),
    )
    assert r.status_code == 200

    expected_workspace_id = str(
        uuid.uuid5(internal._INTERNAL_NAMESPACE, f"workspace:{tenant_id}:default")
    )
    row = asyncio.run(
        db.fetchrow("SELECT id, tenant_id FROM workspaces WHERE id = $1", expected_workspace_id)
    )
    assert row is not None
    assert row["id"] == expected_workspace_id


def test_mint_machine_token_persists_token_hash(client, service_headers):
    _ensure_sqlite_now_function()
    payload = {
        "agent_container_id": "agent-store-hash",
        "tenant_id": "tenant-store-hash",
        "workspace_id": "workspace-store-hash",
    }

    r = client.post(
        "/internal/machine-tokens",
        json=payload,
        headers=_service_headers_for(service_headers, payload["tenant_id"]),
    )

    assert r.status_code == 200
    data = r.json()
    token_hash = data["token_hash"]

    row = asyncio.run(
        db.fetchrow(
            "SELECT token_hash, revoked_at FROM agent_machine_tokens WHERE token_hash = $1",
            token_hash,
        )
    )
    assert row is not None
    assert row["token_hash"] == token_hash
    assert row["revoked_at"] is None


def test_revoke_machine_tokens_requires_service_auth(client):
    r = client.delete("/internal/machine-tokens/agent-no-auth")

    assert r.status_code == 401
    assert r.json()["error"] == "Missing Authorization header"


def test_revoke_machine_tokens_marks_live_tokens_revoked(client, service_headers):
    _ensure_sqlite_now_function()
    tenant_id = "tenant-revoke"
    agent_container_id = "agent-revoke"
    payload = {
        "agent_container_id": agent_container_id,
        "tenant_id": tenant_id,
    }
    headers = _service_headers_for(service_headers, tenant_id)

    first = client.post("/internal/machine-tokens", json=payload, headers=headers)
    second = client.post("/internal/machine-tokens", json=payload, headers=headers)
    other = client.post(
        "/internal/machine-tokens",
        json={**payload, "agent_container_id": "agent-keep"},
        headers=headers,
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert other.status_code == 200

    r = client.delete(f"/internal/machine-tokens/{agent_container_id}", headers=headers)

    assert r.status_code == 200
    data = r.json()
    expected_agent_id = str(uuid.uuid5(internal._INTERNAL_NAMESPACE, agent_container_id))
    expected_workspace_id = str(
        uuid.uuid5(internal._INTERNAL_NAMESPACE, f"workspace:{tenant_id}:default")
    )
    assert data == {
        "agent_container_id": expected_agent_id,
        "revoked_count": 2,
        "workspace_ids": [expected_workspace_id],
    }

    rows = asyncio.run(
        db.fetch(
            "SELECT token_hash, revoked_at FROM agent_machine_tokens "
            "WHERE agent_container_id = $1 ORDER BY token_hash",
            expected_agent_id,
        )
    )
    assert len(rows) == 2
    assert all(row["revoked_at"] is not None for row in rows)

    kept_agent_id = str(uuid.uuid5(internal._INTERNAL_NAMESPACE, "agent-keep"))
    kept_row = asyncio.run(
        db.fetchrow(
            "SELECT revoked_at FROM agent_machine_tokens WHERE agent_container_id = $1",
            kept_agent_id,
        )
    )
    assert kept_row is not None
    assert kept_row["revoked_at"] is None


def test_revoke_machine_tokens_is_tenant_scoped(client, service_headers):
    _ensure_sqlite_now_function()
    payload = {
        "agent_container_id": "agent-tenant-scope",
        "tenant_id": "tenant-owner",
    }
    owner_headers = _service_headers_for(service_headers, payload["tenant_id"])
    wrong_tenant_headers = _service_headers_for(service_headers, "tenant-other")

    minted = client.post("/internal/machine-tokens", json=payload, headers=owner_headers)
    assert minted.status_code == 200

    r = client.delete(
        f"/internal/machine-tokens/{payload['agent_container_id']}",
        headers=wrong_tenant_headers,
    )

    assert r.status_code == 200
    assert r.json()["revoked_count"] == 0

    row = asyncio.run(
        db.fetchrow(
            "SELECT revoked_at FROM agent_machine_tokens WHERE token_hash = $1",
            minted.json()["token_hash"],
        )
    )
    assert row is not None
    assert row["revoked_at"] is None
