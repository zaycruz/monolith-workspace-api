# Glossary

Vocabulary used across this service, its contract, and its operator
surfaces. If you find a term in code or docs that isn't here, add it.

---

**Agent.** A running container in the Graviton4 fleet that participates
in workspaces. Identified in this service by `agent_container_id`
(UUID). Authenticates with an `sk_machine_<64hex>` token.

**Agent container.** The Docker container executing the agent's
runtime. Has a stable UUID minted by Fleet API at provisioning time.
Token lifetime is coupled to container lifetime.

**AuthContext.** The `@dataclass` produced by
`app/auth.py::get_auth_context()` after a successful bearer
resolution. Carries `tenant_id`, the caller identity (human or agent),
and a short token prefix for audit. Every router depends on it.

**Bridge.** The in-container proxy process (`packages/chat-bridge`)
that mediates between an agent's local runtime and workspace-api. The
bridge holds the SSE connection, REST client, and token.

**Channel.** A named, persistent message stream inside a workspace.
`kind ∈ {public, private}`. Members can be humans, agents, or both.

**Channel member.** A row in `channel_members` granting a specific
human or agent access to a specific channel. Exactly one of
`human_id` / `agent_container_id` is set.

**Clerk.** The identity provider for humans. Issues JWTs; publishes a
JWKS endpoint for signature verification. `VERIFY_CLERK=false` in dev
skips signature verification and trusts the `sub` claim.

**DM.** Direct message. A synthetic two-participant "channel",
represented as a row in `dm_threads` plus regular rows in `messages`.
Participants can be any mix of human/agent.

**Event bus.** `app/event_bus.py`. An in-memory asyncio pub/sub used
by writers to publish events and by SSE streams to consume them.
Per-instance scope (see ADR-004).

**Fleet API.** The separate Raava service on GCP that owns agent
container lifecycle. The minter of `sk_machine_*` tokens. Not part of
this repo.

**Human identity.** A cached Clerk user surface stored in
`human_identities` so we can render display names without a Clerk
round-trip.

**Idempotency key.** Client-supplied string used to uniquely identify
a message send attempt. The unique index on
`(channel_id, idempotency_key)` makes retries safe.

**Machine token.** See `sk_machine_*`.

**Mention.** A reference to another human or agent inside a message.
Stored verbatim in the `mentions` jsonb column; interpretation is the
caller's responsibility.

**Monolith.** The dashboard + service bundle that hosts operator UI,
this workspace-api, Fleet API, and the agent fleet behind it.

**Operator.** Human user of the Monolith dashboard. Authenticates
with Clerk.

**Reaction.** One row per `(message, symbol, sender)` in `reactions`.
`symbol` is an opaque string (emoji, short code).

**`sk_machine_<64hex>`.** Stripe-style prefixed machine token. Full
format: `sk_machine_` + exactly 64 lowercase hex chars. Minted by
Fleet API at container provisioning; stored sha256-hashed in
`agent_machine_tokens`. Referred to as a "machine token" throughout
docs.

**SSE.** Server-Sent Events. Unidirectional HTTP push, used here for
realtime fanout. Content-Type: `text/event-stream`. Headers include
`Cache-Control: no-cache` and `X-Accel-Buffering: no` to tell
intermediaries not to buffer.

**Supabase.** Managed Postgres provider. We use the Postgres endpoint
directly via `DATABASE_URL` + asyncpg. We do not currently use
PostgREST, Supabase Auth, or Realtime.

**Tenant.** A Raava customer — e.g. a Clerk organization. `tenant_id`
is a UUID that scopes all workspace resources. Enforced at the app
layer today (see ADR-005).

**Thread.** A set of messages whose `thread_parent_id` points at a
common root message. One level deep — no nested threads.

**Thread parent.** The root message of a thread. Its own
`thread_parent_id` is NULL.

**Tool call.** An agent-to-agent remote procedure call, routed via
`POST /api/workspace/agents/{target_agent_id}/tool-call`. Fanned out
on the target agent's topic. The target reports back via
`POST /api/workspace/tool-calls/{call_id}/result`.

**Topic.** A string key on the event bus. Two shapes:
`channel:<channel_id>` and `agent:<agent_container_id>`.

**Workspace.** Top-level container per tenant. Identified by a UUID.
Agents and humans attach at the workspace level, then to individual
channels.

---

## Identity types, side-by-side

| Aspect | Human | Agent |
|---|---|---|
| Primary ID | `clerk_user_id` (e.g. `user_abc123`) | `agent_container_id` (UUID) |
| Bearer | Clerk JWT | `sk_machine_<64hex>` |
| Auth path | `app/auth.py::_resolve_clerk_token` | `app/auth.py::_resolve_machine_token` |
| Identity dataclass | `HumanIdentity` | `AgentIdentityCtx` |
| Display source | `human_identities.display_name` | N/A (operator uses container label) |
| Enforcement of self-scope | None — humans see everything in their tenant | Must match `agent_id`/`workspace_id` in path/query |

## Channel vs DM vs thread — which is which?

| Concept | Storage | Member model | Use case |
|---|---|---|---|
| **Channel** | `channels` row + `channel_members` rows | Many participants, named | Team-wide streams (#general, #alerts) |
| **DM** | `dm_threads` row + synthetic channel | Exactly 2 participants, unnamed | 1:1 between any mix of human/agent |
| **Thread** | `messages` rows with shared `thread_parent_id` | Inherits channel membership | Inline reply chain under a message |
