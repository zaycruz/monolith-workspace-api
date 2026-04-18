# Decisions (ADRs)

Architectural Decision Records for `monolith-workspace-api`. Each entry
records context, the decision, and consequences. Don't edit past
decisions in place — supersede them with a new ADR.

---

## ADR-001: Host on AWS App Runner (not GCP Cloud Run)

**Date:** 2026-04-17
**Status:** Accepted

### Context

Raava runs two separate clouds:

- **AWS us-east-1** — Graviton4-based EC2 (`r8g.medium`) running agent
  containers. This is policy: agents run on AWS only
  (`project_aws_only_agents` memory).
- **GCP** — Cloud Run hosts Fleet API, plus the Supabase-adjacent
  bits.

We needed to choose which cloud this service runs in. Options:

1. AWS App Runner in us-east-1 (chosen).
2. GCP Cloud Run alongside Fleet API.
3. Self-hosted on the Graviton4 EC2 fleet.

### Decision

Deploy on **AWS App Runner in us-east-1**.

### Rationale

- **Co-located with agents.** Bridges open long-lived SSE connections
  from agent containers. Keeping both in the same AWS region and
  availability zone family minimizes cross-AZ / cross-cloud egress
  cost and latency tail.
- **OIDC parity.** App Runner integrates natively with ECR + Secrets
  Manager + CloudWatch. Deploys from GitHub Actions via OIDC with no
  long-lived keys.
- **Managed scaling.** App Runner's autoscaling (min 1, max 10) fits
  the current operator footprint without needing Kubernetes.
- **Keeps Fleet API on GCP undisturbed.** Fleet API's tight coupling to
  Supabase-hosted Postgres stays on GCP; this service talks to
  Supabase over the public internet either way.

### Consequences

- Cross-cloud ops: two consoles, two billing contexts, two IAM models.
  Acceptable cost for infra-clarity.
- Fleet API → workspace-api calls are public-internet; must be signed
  (we don't have any yet, but the service-to-service future uses a
  dedicated machine token).
- If agents ever move off AWS, revisit.

---

## ADR-002: FastAPI + asyncpg (not Go)

**Date:** 2026-04-17
**Status:** Accepted

### Context

The service is a realtime message-fanout API. The obvious alternatives:

1. FastAPI (Python 3.12) + asyncpg + sse-starlette.
2. Go + chi/echo + pgx + custom SSE.
3. Node/TypeScript + Fastify + pg + @fastify/sse.

### Decision

**FastAPI + asyncpg.**

### Rationale

- **Mirrors Fleet API.** Fleet API is FastAPI + asyncpg. Router
  patterns, exception-handler contract, and the `{"error": "..."}`
  envelope are already learned by the team.
- **Bridge author productivity.** Bridges (`packages/chat-bridge`) are
  Python. Shared schemas and test fixtures cross the repo boundary
  without translation.
- **MVP scale.** Expected peak load is tens of requests/second and
  low-hundreds of concurrent SSE connections — well inside what uvicorn
  with `--workers 2` and asyncpg can absorb without performance
  tuning.

### Consequences

- Python's per-connection memory footprint is higher than Go's. At
  >500 concurrent SSE connections per instance, revisit.
- No native Go-style goroutines; we lean on async/await and asyncpg's
  connection pool.
- GIL is a non-issue because the workload is I/O-bound.

---

## ADR-003: Separate repo (and separate deploy) from Fleet API

**Date:** 2026-04-17
**Status:** Accepted

### Context

Workspace messaging could live inside Fleet API's existing FastAPI app
as a new router subtree. Instead, it sits in its own service, its own
container, its own AWS stack.

### Decision

**Keep workspace-api separate from Fleet API.**

### Rationale

- **Independent release cadence.** Fleet API changes are gated by
  agent-provisioning correctness. Workspace messaging ships on a
  different rhythm — faster, less blast-radius-sensitive.
- **Different scaling profile.** Fleet API is request-response heavy;
  workspace-api carries long-lived SSE connections. Scaling the two
  together wastes capacity on whichever dimension isn't the
  bottleneck.
- **Blast radius containment.** A bug in tool-call routing should not
  be able to corrupt the agent fleet lifecycle.
- **Different cloud.** Fleet API lives on GCP; workspace-api lives on
  AWS. A single repo makes sense for a single-cloud app.

### Consequences

- Two codebases to keep in sync on shared domain concepts (auth,
  tenant model). Contract tests in `packages/chat-bridge/contract/`
  serve as the reconciliation layer.
- Cross-service calls must be authenticated — no shared database.

---

## ADR-004: In-process SSE event bus (for MVP)

**Date:** 2026-04-17
**Status:** Accepted — will be superseded when scale demands

### Context

Realtime fanout options:

1. In-process asyncio pub/sub (chosen, MVP).
2. Redis pub/sub.
3. Supabase Realtime (Postgres LISTEN/NOTIFY behind a managed gateway).

### Decision

Ship with an **in-process event bus** (`app/event_bus.py`). When a
write lands, subscribers on the same instance receive it. Cross-
instance misses are backfilled by the client's `/inbox` poll on
reconnect.

### Rationale

- Zero external dependency for a pre-launch MVP.
- Autoscaling target (100 concurrent connections per instance) plus
  the low concurrent-user count per workspace means most connections
  for a given workspace land on the same instance anyway.
- Clients are already designed to poll `/inbox` — this is a property of
  the bridge contract, not a workaround.

### Consequences

- Cross-instance events are lost. Acceptable for MVP; not acceptable
  at scale.
- Graduate to Redis pub/sub or Supabase Realtime before we exceed
  ~300 concurrent connected agents in a single workspace.

---

## ADR-005: Tenant isolation at the app layer (RLS deferred)

**Date:** 2026-04-17
**Status:** Accepted — RLS planned

### Context

Supabase encourages row-level security keyed on a session-scoped
`tenant_id`. That requires either:

- A custom connection-pool hook that sets the session variable per
  checkout, or
- The Supabase REST API (PostgREST) instead of direct asyncpg.

### Decision

For MVP, enforce tenant isolation **in the application layer** — every
router filters by `AuthContext.tenant_id`. RLS is deferred.

### Rationale

- Direct asyncpg is already wired and matches Fleet API. Switching to
  PostgREST would add a second DB access path for no functional gain
  today.
- App-layer checks are simpler to code-review and audit.
- The cost of a mistake is bounded: service-role DSN + buggy WHERE
  clause is auditable. We have integration tests per router to catch
  regressions.

### Consequences

- A router bug could leak cross-tenant data. Mitigated by:
  - `tenant_id` only ever read from `AuthContext`, never the request
    body.
  - Integration tests assert 404 / 403 on cross-tenant access.
- RLS will land as a follow-up migration once we either move to a
  PG connection pool with a per-checkout session-setter, or cut over
  to Supabase's PostgREST + RLS model.
