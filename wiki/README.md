# workspace-api wiki

Durable, human- and agent-readable docs for `monolith-workspace-api`.

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
