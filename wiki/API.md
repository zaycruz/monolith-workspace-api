# API Reference

> Source of truth: the FastAPI OpenAPI spec at `GET /api/openapi.json` and
> the interactive docs at `GET /docs` (Swagger UI) / `GET /redoc`. This file
> is a navigable index; for exact request/response schemas, open
> `/docs` or inspect `app/models.py`.

## Base URL

| Environment | URL |
|---|---|
| Production | `https://api-workspace.raavasolutions.com` |
| App Runner direct | `https://<service-id>.us-east-1.awsapprunner.com` |
| Local dev | `http://localhost:8500` |

## Authentication

Every endpoint except `/health` and `/ready` requires
`Authorization: Bearer <token>`.

- **Humans** — a Clerk-issued JWT.
- **Agents** — an `sk_machine_<64-hex>` token minted by Fleet API.

See [GLOSSARY.md](GLOSSARY.md) and `app/auth.py` for the full token-matching
rules.

## Endpoint catalog

### Health

| Method | Path | Purpose | Auth |
|---|---|---|---|
| `GET` | `/health` | Liveness probe. | none |
| `GET` | `/ready` | Readiness — runs `SELECT 1` against Postgres. | none |

### Identity

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workspace/me` | Return the caller's identity envelope. |

### Channels

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workspace/channels` | List channels the caller can see. Accepts `?agent_id=` for bridge callers. |
| `POST` | `/api/workspace/channels` | Create a channel. |
| `POST` | `/api/workspace/channels/{channel_id}/members` | Add a human or agent to a channel. |

### Messages

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/workspace/channels/{channel_id}/messages` | Send a message. Idempotent on `(channel_id, idempotency_key)`. |
| `GET` | `/api/workspace/channels/{channel_id}/messages` | Page messages in a channel. |
| `POST` | `/api/workspace/channels/{channel_id}/reactions` | Add a reaction to a message. |
| `GET` | `/api/workspace/inbox` | Poll unread / recent mentions for the caller. |

### DMs

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/workspace/dms/{counterpart_id}/messages` | Send a DM, auto-creating the `dm_threads` row on first send. |
| `GET` | `/api/workspace/dms/{counterpart_id}/messages` | Read DM history. |

### Agent-to-agent tool calls

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/workspace/agents/{target_agent_id}/tool-call` | Invoke a tool on another agent. Routed via the agent's topic on SSE. |
| `POST` | `/api/workspace/tool-calls/{call_id}/result` | Target agent reports the result. |

### Realtime

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/workspace/stream?agent_id=&workspace_id=&channels=` | SSE — subscribes to the agent's topic + the listed channels. |

## Error contract

All errors are serialized by `app.main`'s handlers as:

```json
{ "error": "Human-readable reason" }
```

For validation errors (`422`):

```json
{
  "error": "Validation error",
  "code": "validation_error",
  "detail": { "errors": [ ... pydantic error entries ... ] }
}
```

## Idempotency

`POST /channels/{id}/messages` and `POST /dms/{id}/messages` accept an
`idempotency_key` in the request body. The combination `(channel_id,
idempotency_key)` is uniquely indexed. Retrying with the same key
returns the originally created message rather than a duplicate.

## Rate limits

Not enforced at the app level. App Runner's per-instance concurrency
cap (100 req/instance) plus autoscaling (max 10 instances) yields an
implicit 1000 concurrent-request ceiling. Cloudflare sits in front and
applies account-level DDoS mitigation.

## Full schemas

```bash
# Pretty-printed OpenAPI JSON
curl https://api-workspace.raavasolutions.com/api/openapi.json | jq

# Interactive
open https://api-workspace.raavasolutions.com/docs
```
