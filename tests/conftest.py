"""Test fixtures.

Every test runs against an in-memory SQLite database — no external services
required. The FastAPI TestClient gives us a sync interface into an otherwise
async app, which keeps test code tiny and readable.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

# Select SQLite backend BEFORE importing app.db
os.environ["WORKSPACE_TEST_SQLITE"] = "1"
os.environ["AUTH_ENABLED"] = "true"
os.environ["VERIFY_CLERK"] = "false"

TENANT_ID = "11111111-1111-1111-1111-111111111111"
SERVICE_TOKEN = "sk_service_" + "a" * 64
os.environ["WORKSPACE_SERVICE_TOKEN"] = SERVICE_TOKEN

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

WORKSPACE_ID = "22222222-2222-2222-2222-222222222222"
AGENT_ID_A = "33333333-3333-3333-3333-333333333333"
AGENT_ID_B = "44444444-4444-4444-4444-444444444444"
HUMAN_CLERK_ID = "user_humantest"


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _machine_token(agent_id: str) -> str:
    # Deterministic token per agent so tests can assert round-trip behavior.
    hex64 = hashlib.sha256(agent_id.encode()).hexdigest()
    return f"sk_machine_{hex64}"


def _clerk_jwt(user_id: str, tenant_id: str = TENANT_ID) -> str:
    """Forge an unverified Clerk-style JWT (header.payload.sig).

    Tests run with VERIFY_CLERK=false so signature is never checked.
    """
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = json.dumps(
        {"sub": user_id, "tenant_id": tenant_id, "name": f"display-{user_id}"}
    )
    payload_b64 = (
        base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    )
    return f"{header}.{payload_b64}.sig"


@pytest_asyncio.fixture
async def seeded_db():
    """Initialize the schema and seed one workspace + two machine tokens."""
    await db.init_db()

    # Workspace row
    await db.execute(
        "INSERT INTO workspaces (id, tenant_id, name, created_at) VALUES ($1, $2, $3, $4)",
        WORKSPACE_ID,
        TENANT_ID,
        "test-workspace",
        db.now_iso(),
    )
    # Agent A + Agent B machine tokens
    for agent_id in (AGENT_ID_A, AGENT_ID_B):
        token = _machine_token(agent_id)
        await db.execute(
            """
            INSERT INTO agent_machine_tokens
                (id, token_hash, agent_container_id, workspace_id, tenant_id, created_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            db.new_uuid(),
            _sha256_hex(token),
            agent_id,
            WORKSPACE_ID,
            TENANT_ID,
            db.now_iso(),
        )

    yield

    await db.close_db()


@pytest.fixture
def client(seeded_db):  # noqa: ARG001 — the seeded_db fixture must run first
    with TestClient(app) as c:
        yield c


@pytest.fixture
def agent_a_headers() -> dict:
    return {"Authorization": f"Bearer {_machine_token(AGENT_ID_A)}"}


@pytest.fixture
def agent_b_headers() -> dict:
    return {"Authorization": f"Bearer {_machine_token(AGENT_ID_B)}"}


@pytest.fixture
def human_headers() -> dict:
    return {"Authorization": f"Bearer {_clerk_jwt(HUMAN_CLERK_ID)}"}


@pytest.fixture
def service_headers() -> dict:
    return {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-Id": TENANT_ID,
    }
