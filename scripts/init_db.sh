#!/usr/bin/env bash
# Apply the initial schema to DATABASE_URL.
# Usage: DATABASE_URL=postgres://... ./scripts/init_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${DATABASE_URL:?DATABASE_URL is required}"
psql "$DATABASE_URL" -f alembic/versions/0001_initial.sql
