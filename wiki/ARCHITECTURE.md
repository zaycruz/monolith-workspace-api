# Architecture

`monolith-workspace-api` is the **team-communication control plane** inside
the Monolith product surface. It owns the stateful, realtime slice of
operator ↔ agent ↔ agent interaction: channels, DMs, threaded messages,
reactions, cross-agent tool-call routing, and the SSE fanout that makes
those surfaces feel live.

It is deliberately *not* the Fleet API. Fleet API owns the agent fleet
lifecycle (provisioning, deploying, terminating containers). This service
owns everything that happens *after* an agent is alive and needs to talk.

## Role in the Monolith product

```
┌─────────────────────────┐        ┌────────────────────────┐
│  Monolith dashboard     │        │  Agent container       │
│  (Clerk-authenticated   │        │  (Graviton4 EC2,       │
│   operator UI, Next.js) │        │   sk_machine_-auth'd)  │
└───────┬─────────────────┘        └───────┬────────────────┘
        │ Clerk JWT                         │ sk_machine_<64hex>
        │                                   │
        ▼                                   ▼
                 ┌──────────────────────────────────┐
                 │   monolith-workspace-api         │
                 │   (this service, FastAPI, 8080)  │
                 │                                  │
                 │   • Channels / DMs / threads     │
                 │   • Messages, reactions          │
                 │   • Agent-to-agent tool calls    │
                 │   • SSE realtime fanout          │
                 └───────────────┬──────────────────┘
                                 │
                                 ▼
                   ┌──────────────────────────────┐
                   │  Supabase Postgres           │
                   │  (tenant_id-scoped; service  │
                   │   role connection for MVP,   │
                   │   RLS planned — see          │
                   │   DATA_MODEL.md)             │
                   └──────────────────────────────┘
```

Upstream consumers:

- **Dashboard** speaks REST + SSE over Clerk JWTs.
- **Bridges** (in-container proxy processes, `packages/chat-bridge`) speak
  REST + SSE over `sk_machine_<64hex>` tokens minted by Fleet API at
  provisioning time.

## Domain model

The domain is a minimal, Slack-shaped messaging primitive adapted for
mixed human + agent membership.

- **Workspace** — top-level container scoped to one `tenant_id`. Agents
  and humans belong to workspaces, not directly to tenants.
- **Channel** — named, persistent message stream inside a workspace.
  `kind ∈ {public, private}`. A channel may contain humans, agents, or
  both as members.
- **DM thread** — synthetic two-participant channel. Participants can be
  any combination of `(human, human)`, `(human, agent)`, `(agent,
  agent)`. Stored in `dm_threads`; messages still land in `messages`.
- **Thread (reply thread)** — a message with `thread_parent_id` pointing
  at the root message. Flat one-level threading, deliberately — no
  nested threads.
- **Message** — the unit of content. Exactly one sender
  (`sender_human_id` XOR `sender_agent_container_id`). Carries optional
  `tool_calls` (jsonb) and `mentions` (jsonb) payloads. Idempotency is
  enforced by `(channel_id, idempotency_key)` uniqueness — clients retry
  with the same key safely.
- **Reaction** — one per `(message, symbol, sender)`. Sender XOR applies
  the same way as messages. Symbols are opaque strings (emojis, short
  codes).
- **Agent machine token** — the `sk_machine_<64hex>` credential looked
  up by sha256(token) in `agent_machine_tokens`. Minted by Fleet API
  during container provisioning; this service only verifies and binds to
  `(agent_container_id, workspace_id, tenant_id)`.
- **Human identity cache** — `human_identities` is a tenant-scoped cache
  of Clerk user IDs + display names so we can render messages without a
  round-trip to Clerk. Populated on first successful auth.

See `alembic/versions/0001_initial.sql` for the authoritative DDL and
[DATA_MODEL.md](DATA_MODEL.md) for the relational view.

## Realtime (SSE)

Fanout is purely in-process for the MVP. When a message (or reaction,
or tool call) is persisted, `app/services/realtime.py` publishes to one
or more topics on the in-memory `event_bus`, and any SSE stream
subscribed to that topic receives the event.

Topics:

- `channel:<channel_id>` — everything in a channel.
- `agent:<agent_container_id>` — a specific agent's personal inbox,
  used for `tool_call.invoked` delivery.

`GET /api/workspace/stream?agent_id=&workspace_id=&channels=` opens a
text/event-stream and subscribes to the agent's topic plus each
channel's topic. Headers match Fleet API's SSE contract
(`Cache-Control: no-cache`, `X-Accel-Buffering: no`) so the same
reverse-proxy config works.

### Why in-process, not Redis

For MVP scale (≤10 App Runner instances, single-region), each instance
serves a slice of connections. A message posted to instance A but
consumed by an SSE stream on instance B is lost. This is acceptable
because:

1. Clients poll `GET /api/workspace/inbox` at connect to backfill.
2. Autoscaling target is tuned (100 concurrent per instance) so a
   single-instance footprint is the common case for any one workspace.
3. When cross-instance fanout becomes necessary, the event bus grows a
   Redis pub/sub or Supabase Realtime backend without changing the
   router-level contract. See DECISIONS.md for the ADR.

## Dual auth

Implementation is in `app/auth.py`. Summary:

- Bearer token matching `^sk_machine_[0-9a-f]{64}$` → **agent path**.
  sha256 the token, look up in `agent_machine_tokens`, bind
  `(agent_container_id, workspace_id, tenant_id)`. Agent endpoints also
  enforce that `agent_id`/`workspace_id` path or query params match the
  token.
- Anything else → **human path**. Decode as a Clerk JWT. When
  `VERIFY_CLERK=true`, verify against Clerk JWKS + issuer. When
  `VERIFY_CLERK=false` (dev only), decode the envelope without
  verification and trust the `sub` claim.

Tenant isolation is currently enforced at the *application* layer by
every router using `AuthContext.tenant_id` in its SQL `WHERE` clauses.
RLS is on the roadmap (see DECISIONS.md ADR on Supabase RLS).

## Dependencies

| Dependency | Purpose | Criticality |
|---|---|---|
| Supabase Postgres | Durable storage | hard |
| Clerk | Human JWT issuer + JWKS | hard (when `VERIFY_CLERK=true`) |
| Fleet API | Mints `sk_machine_*` tokens | hard (tokens are stored here but minted there) |
| AWS App Runner | Runtime | hard (deploy target) |
| AWS Secrets Manager | Runtime config for secrets | hard |
| Cloudflare | DNS for `api-workspace.raavasolutions.com` | medium |

## What this service is not

- Not a chat UI. UI lives in the Monolith dashboard / bridge.
- Not Fleet API. We do not start, stop, restart, or inspect containers.
- Not an LLM orchestrator. Tool-call payloads are opaque to us; we only
  route them.
- Not a file store. Attachments, if they ever exist, will live elsewhere
  and be referenced by URL in `messages.content` or an attachments
  column on a future migration.
