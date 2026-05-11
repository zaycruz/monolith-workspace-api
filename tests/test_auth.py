"""Auth behaviors: missing bearer → 401, invalid machine token → 401,
agent path vs human path resolved correctly."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth as auth_module
from tests.conftest import TENANT_ID


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJwksClient:
    def __init__(self, key):
        self._key = key

    def get_signing_key_from_jwt(self, _token):
        return _FakeSigningKey(self._key)


def _verified_settings(issuer: str):
    return SimpleNamespace(
        auth_enabled=True,
        clerk_issuer=issuer,
        clerk_jwks_url="https://clerk.example.test/.well-known/jwks.json",
        verify_clerk=True,
        workspace_service_token="",
    )


def _verified_jwt(private_key, issuer: str, user_id: str = "user_verified") -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": user_id,
            "tenant_id": TENANT_ID,
            "name": f"display-{user_id}",
            "iss": issuer,
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )


def _rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


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


def test_verified_clerk_jwt_me_identity(client, monkeypatch):
    issuer = "https://clerk.example.test"
    private_key, public_key = _rsa_keypair()
    token = _verified_jwt(private_key, issuer)
    monkeypatch.setattr(auth_module, "get_settings", lambda: _verified_settings(issuer))
    monkeypatch.setattr(
        auth_module,
        "_get_clerk_jwks_client",
        lambda _url: _FakeJwksClient(public_key),
    )

    r = client.get("/api/workspace/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 200
    data = r.json()
    assert data["kind"] == "user"
    assert data["display_name"] == "display-user_verified"


def test_verified_clerk_jwt_rejects_wrong_issuer(client, monkeypatch):
    private_key, public_key = _rsa_keypair()
    token = _verified_jwt(private_key, "https://wrong-issuer.example.test")
    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: _verified_settings("https://clerk.example.test"),
    )
    monkeypatch.setattr(
        auth_module,
        "_get_clerk_jwks_client",
        lambda _url: _FakeJwksClient(public_key),
    )

    r = client.get("/api/workspace/me", headers={"Authorization": f"Bearer {token}"})

    assert r.status_code == 401
    assert r.json()["error"] == "Invalid Clerk JWT"


def test_verified_clerk_requires_jwks_and_issuer(client, monkeypatch, human_headers):
    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: SimpleNamespace(
            auth_enabled=True,
            clerk_issuer="",
            clerk_jwks_url="",
            verify_clerk=True,
            workspace_service_token="",
        ),
    )

    r = client.get("/api/workspace/me", headers=human_headers)

    assert r.status_code == 500
    assert r.json()["error"] == "Clerk verification is not configured"


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
