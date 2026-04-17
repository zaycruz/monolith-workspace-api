-- monolith-workspace-api — initial schema
-- Applied idempotently by app/db.py::init_db(), but this file is the durable
-- reference for Supabase migrations when we move off the in-process apply.
-- Format: plain SQL, matches Fleet API conventions (no alembic runtime yet).

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS channels (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    topic TEXT,
    kind TEXT NOT NULL DEFAULT 'public',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workspace_id, name)
);

CREATE TABLE IF NOT EXISTS channel_members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id UUID NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    human_id UUID,
    agent_container_id UUID,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((human_id IS NOT NULL) <> (agent_container_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_channel_members_channel ON channel_members (channel_id);
CREATE INDEX IF NOT EXISTS idx_channel_members_human   ON channel_members (human_id)           WHERE human_id           IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_channel_members_agent   ON channel_members (agent_container_id) WHERE agent_container_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
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
);
CREATE INDEX IF NOT EXISTS idx_messages_channel_created ON messages (channel_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_thread          ON messages (thread_parent_id) WHERE thread_parent_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS reactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL CHECK (symbol IN ('✓','!','?','✗','↻','+')),
    sender_human_id UUID,
    sender_agent_container_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK ((sender_human_id IS NOT NULL) <> (sender_agent_container_id IS NOT NULL)),
    UNIQUE (message_id, symbol, sender_human_id, sender_agent_container_id)
);

CREATE TABLE IF NOT EXISTS agent_machine_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash TEXT NOT NULL UNIQUE,
    agent_container_id UUID NOT NULL,
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    tenant_id UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agent_machine_tokens_live ON agent_machine_tokens (token_hash) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS dm_threads (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id),
    participant_a_human_id UUID,
    participant_a_agent_container_id UUID,
    participant_b_human_id UUID,
    participant_b_agent_container_id UUID,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS human_identities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL,
    clerk_user_id TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- TODO(supabase-rls): Enable RLS on every table and add tenant-scoped policies.
-- For MVP the service speaks to the DB via the service role and enforces
-- tenant isolation at the API layer (see app/auth.py AuthContext.tenant_id).
