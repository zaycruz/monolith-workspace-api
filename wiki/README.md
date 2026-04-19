# workspace-api wiki

Durable, human- and agent-readable docs for `monolith-workspace-api`.

## Staging lane role

- Owner: `engineering/integration`
- Boundary: workspace messaging, SSE fanout, machine-token lookups, and the
  workspace contract consumed by portal and bridge clients
- Shared staging guidance lives in
  [`../../../STAGING_INDEX.md`](../../../STAGING_INDEX.md),
  [`../../../../.factory/library/environment.md`](../../../../.factory/library/environment.md),
  and
  [`../../../../.factory/library/release-commands.md`](../../../../.factory/library/release-commands.md)
- This repo is **supporting**, not a first-class member of the shared Monolith
  staging lane today. The shared lane currently documents the portal
  `https://app-staging.thisismonolith.com` and Fleet API
  `https://api-staging.fleetos.raavasolutions.com`; this repo's checked-in ops
  docs still describe only the separate AWS App Runner service on
  `https://api-workspace.raavasolutions.com`.
- Do **not** invent a workspace-api staging hostname or assume Fleet API
  staging is this service. If a real workspace-api staging lane is added, update
  `OPERATIONS.md`, `infra/terraform/README.md`, and the shared staging runbooks
  together.
- Repo-local commands and identifiers to start from:
  - `uv sync --dev`
  - `./scripts/init_db.sh`
  - `./scripts/run_dev.sh` (local dev port `8500`)
  - `uv run pytest -q`
  - `uv run ruff check`
  - AWS App Runner runtime in `us-east-1`, ECR repo `monolith-workspace-api`,
    and production domain `https://api-workspace.raavasolutions.com` from
    [`OPERATIONS.md`](./OPERATIONS.md)
- This checkout currently has no configured Git remote (`git remote -v` is
  empty), so confirm the canonical remote before scripting deploy, automation,
  or release work for this repo.

| File | When to read |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Before you change any domain model or cross-service contract. |
| [API.md](API.md) | Calling the service from a bridge, dashboard, or agent. |
| [DATA_MODEL.md](DATA_MODEL.md) | Writing SQL, migrations, or thinking about multi-tenancy. |
| [OPERATIONS.md](OPERATIONS.md) | Deploying, rotating secrets, pointing DNS, reading logs. |
| [RUNBOOK.md](RUNBOOK.md) | Something is broken in prod — start here. |
| [DECISIONS.md](DECISIONS.md) | "Why was it done this way?" — architectural decision records. |
| [ONBOARDING.md](ONBOARDING.md) | First hour on this service, human or agent. |
| [GLOSSARY.md](GLOSSARY.md) | Vocabulary: channel vs DM vs thread, sk_machine, etc. |

## Source of truth layering

1. **Code in `app/`** — implementation truth.
2. **`alembic/versions/0001_initial.sql`** — schema truth.
3. **This wiki** — conceptual truth and ops procedure.
4. **`../README.md`** — developer quickstart (subset of ONBOARDING.md).

When code and docs disagree, code wins. Open an issue and fix the doc.

## Contributing to this wiki

- Keep every page self-contained enough to be useful in isolation.
- Prefer concrete commands, ARNs, URLs, and SQL over prose.
- When you update behavior, update the wiki in the same PR. CI does not
  enforce this, but pod manager review does.
