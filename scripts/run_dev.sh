#!/usr/bin/env bash
# Local dev server. Requires `uv sync --dev` first.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8500}" --reload
