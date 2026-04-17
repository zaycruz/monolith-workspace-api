"""Async database access for the workspace API.

Two backends are supported by the same small interface:

* **Postgres** (asyncpg) — production. DATABASE_URL drives a connection pool
  exactly like Fleet API's `services/api/db.py`.
* **SQLite** (aiosqlite) — tests only. Selected by setting the env var
  `WORKSPACE_TEST_SQLITE=1` before import. The schema is a near-identical
  projection of the Postgres DDL (gen_random_uuid() → uuid4 Python-side,
  jsonb → TEXT, timestamptz → TEXT).

The interface is intentionally minimal: `fetch`, `fetchrow`, `execute`, and a
`transaction()` async context manager. Routers build raw SQL strings with $1/$2
placeholders; SQLite execution rewrites those to ? before dispatch.

TODO(supabase-rls): When Supabase is wired in, add tenant_id scoped RLS
policies. Until then, all access is via the service role which requires API-
level tenant checks (see app/auth.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.config import get_settings

logger = logging.getLogger("workspace.db")

USE_SQLITE = os.getenv("WORKSPACE_TEST_SQLITE", "").lower() in ("1", "true", "yes")

# Lazy: imported only on the backend we're using
_pg_pool: Any = None
_sqlite_conn: Any = None


def _parse_dsn(dsn: str) -> dict:
    """Parse DATABASE_URL into asyncpg.create_pool kwargs."""
    parsed = urlparse(dsn)
    params: dict = {
        "user": parsed.username,
        "password": parsed.password,
        "database": parsed.path.lstrip("/") or parsed.hostname,
        "min_size": 2,
        "max_size": 10,
    }
    qs = parse_qs(parsed.query)
    if "host" in qs:
        params["host"] = qs["host"][0]
    elif parsed.hostname:
        params["host"] = parsed.hostname
        if parsed.port:
            params["port"] = parsed.port
    return params


async def init_db() -> None:
    """Initialize the active backend and ensure schema exists."""
    if USE_SQLITE:
        await _init_sqlite()
    else:
        await _init_postgres()


async def close_db() -> None:
    global _pg_pool, _sqlite_conn
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
    if _sqlite_conn is not None:
        await _sqlite_conn.close()
        _sqlite_conn = None


async def _init_postgres() -> None:
    global _pg_pool
    if _pg_pool is not None:
        return
    import asyncpg

    settings = get_settings()
    kwargs = _parse_dsn(settings.database_url)
    logger.info("Connecting to Postgres at %s/%s", kwargs.get("host"), kwargs.get("database"))
    _pg_pool = await asyncpg.create_pool(**kwargs)
    # Apply schema (idempotent)
    async with _pg_pool.acquire() as conn:
        for stmt in _schema_statements_postgres():
            await conn.execute(stmt)


async def _init_sqlite() -> None:
    global _sqlite_conn
    if _sqlite_conn is not None:
        return
    import aiosqlite

    _sqlite_conn = await aiosqlite.connect(":memory:")
    _sqlite_conn.row_factory = aiosqlite.Row
    for stmt in _schema_statements_sqlite():
        await _sqlite_conn.execute(stmt)
    await _sqlite_conn.commit()


# ── Query interface ────────────────────────────────────────────────────────


def _rewrite_placeholders(sql: str) -> str:
    """Convert `$1, $2, ...` to `?` for SQLite, preserving order."""
    return re.sub(r"\$\d+", "?", sql)


async def execute(sql: str, *args: Any) -> None:
    if USE_SQLITE:
        await _sqlite_conn.execute(_rewrite_placeholders(sql), args)
        await _sqlite_conn.commit()
    else:
        async with _pg_pool.acquire() as conn:
            await conn.execute(sql, *args)


async def fetchrow(sql: str, *args: Any) -> dict[str, Any] | None:
    if USE_SQLITE:
        cur = await _sqlite_conn.execute(_rewrite_placeholders(sql), args)
        row = await cur.fetchone()
        await cur.close()
        return dict(row) if row else None
    async with _pg_pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
    if USE_SQLITE:
        cur = await _sqlite_conn.execute(_rewrite_placeholders(sql), args)
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]
    async with _pg_pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


@asynccontextmanager
async def transaction() -> AsyncIterator[None]:
    if USE_SQLITE:
        # aiosqlite autocommits on .commit(); use a SAVEPOINT for nesting safety.
        sp = f"sp_{uuid.uuid4().hex[:8]}"
        await _sqlite_conn.execute(f"SAVEPOINT {sp}")
        try:
            yield
            await _sqlite_conn.execute(f"RELEASE SAVEPOINT {sp}")
            await _sqlite_conn.commit()
        except BaseException:
            await _sqlite_conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            raise
    else:
        async with _pg_pool.acquire() as conn, conn.transaction():
            yield


# ── JSON helpers — jsonb stored as TEXT in SQLite ──────────────────────────


def dump_json(value: Any) -> Any:
    """Serialize jsonb-bound values for the active backend."""
    if USE_SQLITE:
        return json.dumps(value) if value is not None else None
    # asyncpg serializes Python lists/dicts to jsonb automatically when column is jsonb.
    return value


def load_json(value: Any) -> Any:
    """Deserialize jsonb-bound values from the active backend."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


# ── Schema ─────────────────────────────────────────────────────────────────


def _schema_statements_postgres() -> list[str]:
    return [
        """CREATE TABLE IF NOT EXISTS workspaces (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS channels (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            topic TEXT,
            kind TEXT NOT NULL DEFAULT 'public',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (workspace_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS channel_members (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            human_id UUID,
            agent_container_id UUID,
            joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK ((human_id IS NOT NULL) <> (agent_container_id IS NOT NULL))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_channel_members_channel ON channel_members (channel_id)",
        "CREATE INDEX IF NOT EXISTS idx_channel_members_human ON channel_members (human_id) WHERE human_id IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS idx_channel_members_agent ON channel_members (agent_container_id) WHERE agent_container_id IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS messages (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
            thread_parent_id UUID REFERENCES messages(id) ON DELETE CASCADE,
            sender_human_id UUID,
            sender_agent_container_id UUID,
            content TEXT NOT NULL,
            tool_calls JSONB,
            mentions JSONB,
            idempotency_key TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK ((sender_human_id IS NOT NULL) <> (sender_agent_container_id IS NOT NULL)),
            UNIQUE (channel_id, idempotency_key)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages (channel_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages (thread_parent_id) WHERE thread_parent_id IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS reactions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            symbol TEXT NOT NULL,
            sender_human_id UUID,
            sender_agent_container_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CHECK ((sender_human_id IS NOT NULL) <> (sender_agent_container_id IS NOT NULL)),
            UNIQUE (message_id, symbol, sender_human_id, sender_agent_container_id)
        )""",
        """CREATE TABLE IF NOT EXISTS agent_machine_tokens (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            token_hash TEXT NOT NULL UNIQUE,
            agent_container_id UUID NOT NULL,
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            tenant_id UUID NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            revoked_at TIMESTAMPTZ
        )""",
        "CREATE INDEX IF NOT EXISTS idx_agent_machine_tokens_live ON agent_machine_tokens (token_hash) WHERE revoked_at IS NULL",
        """CREATE TABLE IF NOT EXISTS dm_threads (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id UUID NOT NULL REFERENCES workspaces(id),
            participant_a_human_id UUID,
            participant_a_agent_container_id UUID,
            participant_b_human_id UUID,
            participant_b_agent_container_id UUID,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
        # Human identities — minimal row so we can render display names without
        # a round-trip to Clerk. Clerk remains authoritative; this table is
        # a cache populated on first successful auth.
        """CREATE TABLE IF NOT EXISTS human_identities (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL,
            clerk_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )""",
    ]


def _schema_statements_sqlite() -> list[str]:
    # SQLite projection: UUIDs are TEXT (Python uuid4().hex); timestamps are
    # TEXT ISO8601; jsonb is TEXT. Partial indexes are dropped since not all
    # SQLite versions support them in aiosqlite's bundled build.
    return [
        """CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS channels (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            topic TEXT,
            kind TEXT NOT NULL DEFAULT 'public',
            created_at TEXT NOT NULL,
            UNIQUE (workspace_id, name)
        )""",
        """CREATE TABLE IF NOT EXISTS channel_members (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            human_id TEXT,
            agent_container_id TEXT,
            joined_at TEXT NOT NULL,
            CHECK ((human_id IS NOT NULL) <> (agent_container_id IS NOT NULL))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_channel_members_channel ON channel_members (channel_id)",
        """CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL,
            thread_parent_id TEXT,
            sender_human_id TEXT,
            sender_agent_container_id TEXT,
            content TEXT NOT NULL,
            tool_calls TEXT,
            mentions TEXT,
            idempotency_key TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK ((sender_human_id IS NOT NULL) <> (sender_agent_container_id IS NOT NULL)),
            UNIQUE (channel_id, idempotency_key)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages (channel_id, created_at DESC)",
        """CREATE TABLE IF NOT EXISTS reactions (
            id TEXT PRIMARY KEY,
            message_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            sender_human_id TEXT,
            sender_agent_container_id TEXT,
            created_at TEXT NOT NULL,
            CHECK ((sender_human_id IS NOT NULL) <> (sender_agent_container_id IS NOT NULL)),
            UNIQUE (message_id, symbol, sender_human_id, sender_agent_container_id)
        )""",
        """CREATE TABLE IF NOT EXISTS agent_machine_tokens (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            agent_container_id TEXT NOT NULL,
            workspace_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            revoked_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS dm_threads (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            participant_a_human_id TEXT,
            participant_a_agent_container_id TEXT,
            participant_b_human_id TEXT,
            participant_b_agent_container_id TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS human_identities (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            clerk_user_id TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
    ]


def new_uuid() -> str:
    """Generate a UUID string — used when the backend is SQLite (which has
    no gen_random_uuid()) and by callers that want a stable ID before the
    INSERT returns."""
    return str(uuid.uuid4())


def now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
