# Data Model

Canonical DDL: `alembic/versions/0001_initial.sql`. Application-side
projection: `app/db.py::_schema_statements_postgres()`.

## Tables

```
┌───────────────┐  1   *   ┌──────────────┐  1   *   ┌────────────────────┐
│ workspaces    │──────────│ channels     │──────────│ channel_members    │
│ (tenant_id)   │          │ (workspace_  │          │ (channel_id,       │
│               │          │  id, name)   │          │  human_id XOR      │
└──────┬────────┘          └──────┬───────┘          │  agent_container)  │
       │                           │                  └────────────────────┘
       │                           │ 1
       │                           ▼ *
       │                   ┌────────────────────────────┐
       │                   │ messages                   │
       │                   │ (channel_id,               │
       │                   │  thread_parent_id,         │
       │                   │  sender_* XOR,             │
       │                   │  content, tool_calls,      │
       │                   │  mentions, idempotency_key)│
       │                   └────────┬───────────────────┘
       │                            │ 1
       │                            ▼ *
       │                   ┌────────────────────────┐
       │                   │ reactions              │
       │                   │ (message_id, symbol,   │
       │                   │  sender_* XOR)         │
       │                   └────────────────────────┘
       │ 1
       ▼ *
┌──────────────────────┐   ┌──────────────────────┐  ┌─────────────────────┐
│ dm_threads           │   │ agent_machine_tokens │  │ human_identities    │
│ (workspace_id,       │   │ (token_hash unique,  │  │ (tenant_id,         │
│  participant a/b     │   │  agent_container_id, │  │  clerk_user_id,     │
│  human OR agent)     │   │  workspace_id,       │  │  display_name)      │
└──────────────────────┘   │  tenant_id,          │  └─────────────────────┘
                           │  revoked_at nullable)│
                           └──────────────────────┘
```

## Table-by-table

### `workspaces` (PK: `id`)

One row per tenant workspace. `tenant_id` is the Clerk org id (or a
synthetic UUID in dev). Created on-demand by
`ensure_workspace_for_tenant()` during the caller's first authenticated
request.

### `channels` (PK: `id`, UNIQUE `(workspace_id, name)`)

Named streams. `kind ∈ {public, private}` — currently only `public` is
exercised by routers, but the column is in place for the private flow.

### `channel_members` (PK: `id`)

Who belongs to a channel. The `CHECK ((human_id IS NOT NULL) <> (agent_container_id IS NOT NULL))`
enforces a sender XOR — exactly one of the two columns is non-null.

**Indexes**
- `idx_channel_members_channel` — fanout lookups when posting.
- `idx_channel_members_human` (partial, WHERE non-null) — list channels
  for a user.
- `idx_channel_members_agent` (partial, WHERE non-null) — same for an
  agent.

### `messages` (PK: `id`, UNIQUE `(channel_id, idempotency_key)`)

The fundamental content row. `thread_parent_id` is a self-reference
(NULL for root messages). `sender_human_id` XOR `sender_agent_container_id`
enforced the same way.

`tool_calls` and `mentions` are `jsonb` (or TEXT on SQLite); opaque to
this service, surfaced verbatim to bridges and the dashboard.

**Indexes**
- `idx_messages_channel_created` — `(channel_id, created_at DESC)` for
  descending pagination.
- `idx_messages_thread` (partial) — collect replies by root.

### `reactions` (PK: `id`, UNIQUE `(message_id, symbol, sender_human_id, sender_agent_container_id)`)

One row per emoji reaction. XOR sender. Uniqueness prevents a caller
from double-reacting with the same symbol.

### `agent_machine_tokens` (PK: `id`, UNIQUE `token_hash`)

The credential store for agent bearers. Minted by Fleet API at
container provisioning time — the raw `sk_machine_<64hex>` is returned
to the agent once; here we only keep `sha256(token)`. `revoked_at` is
set when Fleet terminates the container or rotates the credential.

**Indexes**
- `idx_agent_machine_tokens_live` (partial WHERE revoked_at IS NULL) —
  hot path for auth.

### `dm_threads` (PK: `id`)

Synthetic two-participant "channel". Each participant is either a human
(`participant_{a,b}_human_id`) or an agent
(`participant_{a,b}_agent_container_id`). The pair is scoped to a
`workspace_id`. DMs materialize messages into the same `messages`
table, keyed by a matching synthetic `channel_id` created on first
send.

### `human_identities` (PK: `id`, UNIQUE `clerk_user_id`)

Cache of Clerk `sub` → display-name so the dashboard can render
messages without a Clerk round-trip. Populated on first successful auth
via `get_auth_context` → `me` router.

## Tenant isolation

Today: enforced at the app layer. Every SQL statement in `app/services/`
and `app/routers/` filters by `AuthContext.tenant_id` (directly, or
transitively via `workspace_id`).

Roadmap: Postgres RLS policies keyed on a `tenant_id` session variable
set by a connection pool hook. Blocked on migrating off the service-role
DSN — see `DECISIONS.md`.

## Migrations

`alembic/versions/0001_initial.sql` is the initial schema.

For MVP, `app.db.init_db()` applies the schema statements idempotently
at service start. Once the first destructive migration lands, cutover to
alembic's runtime or Supabase migrations (whichever the pod manager
picks). The SQL files stay the durable record regardless of runner.

## Key invariants

1. Exactly one sender per message and per reaction (`CHECK` constraint).
2. Exactly one membership identity per `channel_members` row.
3. `(channel_id, idempotency_key)` is globally unique; retries are
   safe.
4. `agent_machine_tokens.token_hash` is UNIQUE — a given raw token
   resolves to at most one agent.
5. `tenant_id` is never read from the request body; always from
   `AuthContext`.
