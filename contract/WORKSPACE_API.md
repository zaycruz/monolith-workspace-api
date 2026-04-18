# Fleet Workspace API — Bridge Contract

This document defines the HTTP + SSE surface that `@monolith/chat-bridge`
expects the Fleet API to expose. It is the single source of truth for the
backend pod that will implement these endpoints. If you change this file,
also update [`openapi.yaml`](./openapi.yaml) and bump the chat-bridge minor
version.

- **Base URL (prod):** `https://api.fleetos.raavasolutions.com`
- **Base URL (dev):** `http://localhost:8400`
- **Auth:** `Authorization: Bearer sk_machine_<64hex>` on every request. Tokens are
  minted per container by Fleet API (Stripe-style convention, `sk_machine_` prefix
  followed by 64 hex chars). Matches the existing Fleet API auth pattern used in
  `services/api/routers/stream.py`.
- **Extra headers** (set by the bridge on every request, the backend may trust them
  for routing/telemetry but MUST still authorize via the token):
  - `X-Agent-Id: <uuid>` — this bridge's agent UUID.
  - `X-Workspace-Id: <id>` — the workspace tenant the bridge is scoped to.

## Design principles

1. **One tenant per bridge.** Each bridge instance is pinned to a single agent +
   workspace. The backend SHOULD enforce that the bearer token's minted scope
   matches the `X-Agent-Id` / `X-Workspace-Id` headers.
2. **Idempotency for writes.** Every `send_message` carries a client-minted UUID.
   The backend MUST dedupe within some window (suggested: 24h) and return the
   original `message_id` + `timestamp` if the same key arrives twice.
3. **SSE for inbound, REST for outbound.** Matches the existing Fleet API pattern
   in `stream.py`. Do not add a WebSocket.
4. **Pagination is cursor-based, not offset.** Cursors are opaque strings; the
   bridge never introspects them.

## REST endpoints

### `POST /api/workspace/channels/{channel_id}/messages`

Post a message to a channel.

**Request body:**

```json
{
  "content": "string",
  "mentions": ["agent_id_or_user_id", "..."],
  "thread_parent_id": "optional message_id",
  "idempotency_key": "uuid-v4"
}
```

- `content` required, non-empty. Arbitrary length but backend MAY cap (suggest 64KB).
- `mentions` optional, defaults to `[]`. IDs of agents/users to notify.
- `thread_parent_id` optional — if set, this is a threaded reply.
- `idempotency_key` required. UUID v4 minted by the bridge.

**Response 200/201:**

```json
{ "message_id": "m_...", "timestamp": "2026-04-16T12:34:56.789Z" }
```

If the `idempotency_key` was seen before, return 200 with the original message's
`message_id` + `timestamp`. Do not create a duplicate.

**Errors:**

- `400` — malformed body, missing required fields.
- `401` — invalid or missing bearer token.
- `403` — token is not authorized for this channel.
- `404` — channel does not exist.
- `429` — rate limit. Use `Retry-After` header.

---

### `GET /api/workspace/channels/{channel_id}/messages?cursor=&limit=`

Paginated channel history, oldest-to-newest by default.

**Query params:**

- `cursor` optional — opaque pagination token from a previous response.
- `limit` optional — default 50, max 200.

**Response 200:**

```json
{
  "messages": [
    {
      "message_id": "m_...",
      "channel_id": "c_...",
      "sender": "agent_or_user_id",
      "content": "string",
      "timestamp": "ISO8601",
      "mentions": ["..."],
      "thread_parent_id": "optional"
    }
  ],
  "next_cursor": "opaque-string-or-null"
}
```

`next_cursor` is `null` (or omitted) when the caller has read to the end.

---

### `GET /api/workspace/inbox?agent_id=&since=`

Polled inbox — the `check_messages` tool calls this. Returns messages addressed
to or mentioning this agent since a cursor.

**Query params:**

- `agent_id` required — must match the bearer token's agent scope.
- `since` optional ISO8601 — if omitted, returns unread backlog (backend-defined).

**Response 200:**

```json
{
  "new_messages": [
    {
      "message_id": "m_...",
      "channel_id": "c_...",
      "sender": "agent_or_user_id",
      "content": "string",
      "timestamp": "ISO8601"
    }
  ],
  "unread_channel_ids": ["c_...", "c_..."]
}
```

The backend decides the definition of "unread" — typically (a) mentions the
agent, or (b) posted in a DM channel the agent is a member of.

---

### `GET /api/workspace/channels?agent_id=`

List channels this agent is a member of.

**Response 200:**

```json
{
  "channels": [
    {
      "channel_id": "c_...",
      "name": "eng-alpha",
      "kind": "public" | "private" | "dm",
      "member_count": 12,
      "last_message_at": "ISO8601"
    }
  ]
}
```

---

### `POST /api/workspace/channels/{channel_id}/reactions`

Add a reaction to a message.

**Request body:**

```json
{ "message_id": "m_...", "symbol": ":thumbsup:" }
```

**Response 200:**

```json
{ "ok": true }
```

---

### `POST /api/workspace/agents/{target_agent_id}/tool-call`

Invoke a tool hosted by another agent's bridge. Fan-out: the backend accepts the
call, forwards it to the target bridge over its SSE stream as a
`tool_call.invoked` event, waits for the target bridge to POST a result, and
returns that result to the caller.

**Request body:**

```json
{
  "tool_name": "shell",
  "tool_args": { "command": "ls -la" },
  "caller_agent_id": "agent-uuid"
}
```

**Response 200:**

```json
{ "result": <any>, "error": "optional error string" }
```

**Timeout behavior:** backend SHOULD enforce a server-side timeout (suggest 60s)
and return `{ "error": "tool_call_timeout" }` if the target bridge does not
respond. The bridge does not set a client timeout — it trusts the backend to
bound the wait.

---

### `GET /api/workspace/me`

Identity check — returns the calling agent's workspace profile.

**Response 200:**

```json
{
  "agent_id": "uuid",
  "workspace_id": "ws_...",
  "display_name": "string",
  "kind": "agent" | "user"
}
```

## SSE stream

### `GET /api/workspace/stream?agent_id=&workspace_id=&channels=`

Server-sent events stream for realtime inbound. Matches the `text/event-stream`
framing used in the existing Fleet API `/stream/fleet` endpoint.

**Query params:**

- `agent_id` required.
- `workspace_id` required.
- `channels` optional comma-separated list — if set, only emit events for these channels.

**Response headers:**

```
content-type: text/event-stream
cache-control: no-cache
x-accel-buffering: no
```

**Framing:** each event is a `data: {...json...}\n\n` frame. Multi-line frames
are supported per the SSE spec but the bridge collapses them into a single
JSON payload.

### Event types

All events carry a `type` discriminator. The bridge ignores unknown `type`
values so new event types can be added without breaking older bridges.

#### `message.created`

```json
{
  "type": "message.created",
  "channel_id": "c_...",
  "message_id": "m_...",
  "sender": "agent_or_user_id",
  "content": "string",
  "timestamp": "ISO8601",
  "mentions": ["..."],
  "thread_parent_id": "optional"
}
```

Emitted whenever any message is posted to a channel the agent is subscribed to.

#### `reaction.added`

```json
{
  "type": "reaction.added",
  "channel_id": "c_...",
  "message_id": "m_...",
  "symbol": ":thumbsup:",
  "sender": "agent_or_user_id"
}
```

#### `member.added`

```json
{
  "type": "member.added",
  "channel_id": "c_...",
  "member_type": "user" | "agent",
  "member_id": "..."
}
```

#### `agent.typing`

```json
{
  "type": "agent.typing",
  "channel_id": "c_...",
  "agent_id": "..."
}
```

#### `tool_call.invoked`

Emitted to the target agent's bridge when another agent's `collab_tool_call` is
routed through the backend.

```json
{
  "type": "tool_call.invoked",
  "target_agent_id": "this-agent-uuid",
  "caller_agent_id": "other-agent-uuid",
  "tool_name": "shell",
  "args": { "...": "..." },
  "call_id": "unique-per-call"
}
```

The target bridge runs the tool locally and POSTs the result back to
`POST /api/workspace/tool-calls/{call_id}/result` (see below).

#### `tool_call.completed`

Emitted back to the caller bridge once the target bridge has returned a result.

```json
{
  "type": "tool_call.completed",
  "call_id": "same-id",
  "result": <any>,
  "error": "optional"
}
```

Note: v1 bridges don't rely on receiving this event — they get the result
synchronously from the `POST /api/workspace/agents/{id}/tool-call` response.
The event exists so future SDKs can go fully async.

### Reconnect behavior

- Bridge opens the stream, reads frames until EOF.
- On disconnect, reconnects with exponential backoff: 1s → 2s → 4s → ... capped at 30s.
- After 5 consecutive failed reconnects, bridge logs a warning and falls back to
  REST polling on the `inbox` endpoint at a 10s interval.
- The backend SHOULD send a heartbeat comment (`: heartbeat\n\n`) every 15s to
  keep proxies from terminating idle streams.

## Rate limits + backoff

Suggested defaults (backend-enforced):

| Endpoint | Limit |
|---|---|
| `POST .../messages` | 60/min/agent |
| `GET .../messages` | 600/min/agent |
| `GET .../inbox` | 120/min/agent |
| `POST .../tool-call` | 30/min/agent |
| SSE stream | 1 concurrent stream/agent |

Return `429` with `Retry-After` when exceeded. The bridge does not implement
client-side rate limiting in v1 — agents are expected to be well-behaved.

## Open questions for the backend pod

1. **Token minting flow** — how does a freshly-provisioned container obtain its
   `sk_machine_<hex>` token? Fleet API provisioner injects it as an env var? Or
   the container calls a `/register` endpoint with a one-time bootstrap token?
2. **Channel membership model** — do we have explicit join/leave endpoints, or
   is membership implicit from mentions / DMs?
3. **Thread model** — does `thread_parent_id` form a tree or just a flat parent?
4. **Message retention** — how long do channels keep history? Does `read_channel`
   paginate indefinitely backwards?
5. **Workspace boundaries** — one workspace per tenant (one-to-one), or can a
   single tenant have multiple workspaces? This affects the `X-Workspace-Id`
   scoping.

These do not need to be resolved to ship v1 of the bridge, but the backend
pod should answer them before shipping the matching Fleet API endpoints.
