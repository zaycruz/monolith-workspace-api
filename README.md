# monolith-workspace-api

FastAPI service that owns **workspace messaging** for the Monolith product:
channels, DMs, threads, messages, reactions, and agent-to-agent tool-call
routing with SSE realtime.

The wire contract is already documented in
[`packages/chat-bridge/contract/WORKSPACE_API.md`](../../packages/chat-bridge/contract/WORKSPACE_API.md)
and [`openapi.yaml`](../../packages/chat-bridge/contract/openapi.yaml). This
service implements that contract — treat the contract as source of truth.

## Why a separate service?

Fleet API (`services/api/`) manages container lifecycle. Workspace messaging
is a fundamentally different shape: read-heavy SSE fanout, idempotent writes
at high volume, per-channel topic routing. Splitting lets each scale on its
own curve without contention.

## Run (local)

```bash
# Install deps
uv sync --dev

# Configure
cp .env.example .env
# Edit DATABASE_URL at minimum. For Supabase:
#   DATABASE_URL=postgresql://postgres:<pw>@<project-ref>.supabase.co:5432/postgres

# Initialize schema (Postgres)
./scripts/init_db.sh

# Run
./scripts/run_dev.sh
# → listening on :8500
```

## Test

```bash
uv run pytest -q
```

Tests run against an in-memory SQLite (`WORKSPACE_TEST_SQLITE=1`) — no
Postgres / Supabase required.

## Lint

```bash
uv run ruff check
```

## Schema

```
workspaces                one per tenant (MVP)
  └── channels
        ├── channel_members   (polymorphic: human xor agent)
        └── messages
              ├── reactions       (fixed symbol set: ✓ ! ? ✗ ↻ +)
              └── (thread tree via thread_parent_id)

agent_machine_tokens       sha256(sk_machine_<hex>) lookup table
dm_threads                 synthetic 1:1 channels
human_identities           display-name cache keyed on clerk_user_id
```

See [`alembic/versions/0001_initial.sql`](alembic/versions/0001_initial.sql)
for the full DDL.

## Auth

Three token kinds are accepted on `Authorization: Bearer <token>`:

| Token shape | Used by | Resolution |
|---|---|---|
| `sk_service_[0-9a-f]{64}` | Fleet API service calls | constant-time compare with `WORKSPACE_SERVICE_TOKEN` plus `X-Tenant-Id` |
| `sk_machine_[0-9a-f]{64}` | Agents (chat-bridge, worker containers) | sha256-hash → `agent_machine_tokens` lookup |
| Clerk JWT | Humans (portal, dashboards) | JWKS verification (prod) / unverified decode (`VERIFY_CLERK=false`) |

The dependency `get_auth_context()` returns an `AuthContext` carrying
`tenant_id` plus a discriminated `identity` (human vs agent).

For production Clerk verification, set:

```bash
VERIFY_CLERK=true
CLERK_JWKS_URL=https://clerk.thisismonolith.com/.well-known/jwks.json
CLERK_ISSUER=https://clerk.thisismonolith.com
```

## Endpoints

| Method | Path | Source of truth |
|---|---|---|
| `GET`  | `/health`, `/ready` | — |
| `POST` | `/internal/machine-tokens` | service-only machine-token minting |
| `DELETE` | `/internal/machine-tokens/{agent_container_id}` | service-only tenant-scoped machine-token revocation |
| `GET`  | `/api/workspace/me` | openapi `getMe` |
| `GET`  | `/api/workspace/channels` | openapi `listChannels` |
| `POST` | `/api/workspace/channels` | *not in contract — bootstrap helper* |
| `POST` | `/api/workspace/channels/{id}/members` | *not in contract — bootstrap helper* |
| `POST` | `/api/workspace/channels/{id}/messages` | openapi `sendMessage` |
| `GET`  | `/api/workspace/channels/{id}/messages` | openapi `readChannel` |
| `POST` | `/api/workspace/channels/{id}/reactions` | openapi `addReaction` |
| `GET`  | `/api/workspace/inbox` | openapi `checkMessages` |
| `POST` | `/api/workspace/dms/{counterpart_id}/messages` | *DM convenience, not in openapi* |
| `GET`  | `/api/workspace/dms/{counterpart_id}/messages` | *DM convenience, not in openapi* |
| `POST` | `/api/workspace/agents/{id}/tool-call` | openapi `collabToolCall` |
| `POST` | `/api/workspace/tool-calls/{call_id}/result` | companion to `tool_call.invoked` |
| `GET`  | `/api/workspace/stream` | openapi `stream` |

## SSE event types

Frames are `data: {json}\n\n` with `id:` for Last-Event-ID resume. Heartbeat
comment (`: heartbeat\n\n`) every 15s. All types match the openapi
`WorkspaceEvent` discriminated union:

- `message.created`
- `reaction.added`
- `member.added`
- `agent.typing`
- `tool_call.invoked`
- `tool_call.completed`

## Known deferred items

- **Production Clerk env rollout** — JWKS verification is implemented, but
  production must set `VERIFY_CLERK=true`, `CLERK_JWKS_URL`, and `CLERK_ISSUER`
  only after staging validation with a real Clerk session.
- **Redis pub/sub for multi-replica SSE** — current fanout is in-process
  (`app/event_bus.py`). Single-replica is fine for MVP; multi-replica needs
  Redis or NATS so every instance sees every publish.
- **Supabase RLS policies** — schema is designed for tenant-scoped RLS but
  none is enabled yet. All tenant isolation is enforced in `app/auth.py`
  and routing helpers. Before exposing the DB to third-party integrations,
  enable RLS.
- **Rate limiting** — contract specifies per-endpoint limits (60/min,
  120/min, etc). No enforcement yet; add a `slowapi`-backed middleware.

Grep for `TODO(contract-gap)` and `TODO(...)` to find all markers.
