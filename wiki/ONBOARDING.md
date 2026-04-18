# Onboarding — workspace-api

First hour on this service, human or agent. Read the top of
[ARCHITECTURE.md](ARCHITECTURE.md) first if you don't know what this
service does.

## What you need installed

- Python 3.12
- [uv](https://docs.astral.sh/uv/) 0.5.x
- Docker 24+ (for container builds)
- AWS CLI v2 (for infra / deploys)
- Terraform >= 1.6 (for infra changes)

## Clone + run locally

```bash
git clone <repo-url>
cd workspace-api

cp .env.example .env
# Leave VERIFY_CLERK=false for dev — no Clerk account required.

uv sync --extra dev
./scripts/run_dev.sh
# Serves on http://localhost:8500
```

Open the interactive docs at `http://localhost:8500/docs`.

## Run the tests

```bash
# Uses in-memory SQLite; no Postgres required.
WORKSPACE_TEST_SQLITE=1 AUTH_ENABLED=false uv run pytest -q
```

Lint:

```bash
uv run ruff check .
```

## Build the container locally

```bash
docker build -t monolith-workspace-api:dev .
docker run --rm -p 8080:8080 \
  -e AUTH_ENABLED=false \
  -e WORKSPACE_TEST_SQLITE=1 \
  monolith-workspace-api:dev
# (Note: WORKSPACE_TEST_SQLITE requires aiosqlite, which is dev-only.
#  For prod builds, use a real DATABASE_URL.)
```

For a production-style run, point at a real Postgres:

```bash
docker run --rm -p 8080:8080 \
  -e DATABASE_URL="postgresql://postgres:postgres@host.docker.internal:5432/workspace" \
  -e AUTH_ENABLED=false \
  monolith-workspace-api:dev
```

## Where things live

```
services/workspace-api/
├── app/                      # FastAPI application
│   ├── main.py               # app instantiation + lifespan
│   ├── config.py             # pydantic-settings Settings
│   ├── db.py                 # async Postgres + SQLite test fallback
│   ├── auth.py               # dual bearer auth (Clerk + sk_machine)
│   ├── event_bus.py          # in-process pub/sub
│   ├── models.py             # pydantic request/response schemas
│   ├── routers/              # one module per API namespace
│   └── services/             # business logic, called from routers
├── alembic/versions/*.sql    # schema source of truth
├── tests/                    # pytest suite
├── scripts/                  # run_dev.sh, init_db.sh
├── infra/terraform/          # AWS App Runner + ECR stack
├── .github/workflows/        # CI + deploy
├── Dockerfile
├── apprunner.yaml            # reference config (not used for image deploys)
└── wiki/                     # this folder
```

## Where things happen

| Task | Start here |
|---|---|
| Add an endpoint | `app/routers/<namespace>.py` + matching model in `app/models.py` + test in `tests/` |
| Add a domain primitive | SQL in a new `alembic/versions/NNNN_*.sql` + mirror in `app/db.py::_schema_statements_*()` + service in `app/services/` |
| Change auth rules | `app/auth.py` + `tests/test_auth.py` |
| Adjust SSE topics | `app/event_bus.py` + `app/services/realtime.py` + `app/routers/stream.py` |
| Change a secret's name | `infra/terraform/variables.tf::secrets` + operator creates/renames in Secrets Manager |
| Change deploy flow | `.github/workflows/deploy.yml` + `infra/terraform/main.tf` |

## Useful commands

```bash
# Open interactive API docs
open http://localhost:8500/docs

# Run a single test
uv run pytest tests/test_messages.py::test_send_and_read -q

# Type check (best-effort — mypy is lax in this repo)
uv run mypy app

# Format
uv run ruff format .

# Tail production logs
aws logs tail /aws/apprunner/monolith-workspace-api/application \
  --since 10m --region us-east-1 --follow
```

## Reading order for a new contributor

1. [ARCHITECTURE.md](ARCHITECTURE.md) — domain + topology.
2. [DATA_MODEL.md](DATA_MODEL.md) — the 7 tables and invariants.
3. [API.md](API.md) — endpoint catalog, then `GET /docs` for exact
   schemas.
4. [OPERATIONS.md](OPERATIONS.md) — how it gets to production.
5. [DECISIONS.md](DECISIONS.md) — why the service looks the way it does.
6. [RUNBOOK.md](RUNBOOK.md) — skim once; read closely when something
   breaks.
7. [GLOSSARY.md](GLOSSARY.md) — keep open while reading the others.

## First-PR checklist

- `uv run ruff check .` passes.
- `uv run pytest -q` passes with `WORKSPACE_TEST_SQLITE=1`.
- New endpoints have tests in `tests/`.
- New env vars appear in `.env.example`, `app/config.py`, and (if
  secret) `infra/terraform/variables.tf::secrets`.
- Wiki updated if behavior changed.
