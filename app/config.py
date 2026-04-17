"""Settings loaded from environment / .env via pydantic-settings.

Matches Fleet API's pattern of `os.getenv(...)` but uses pydantic-settings for
stronger typing and early validation.  No runtime branching on environment in
this file — all feature switches are explicit env vars.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).parent.parent / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server
    host: str = "0.0.0.0"
    port: int = 8500
    base_url: str = "http://localhost:8500"

    # Database
    # Tests may override this with an in-process SQLite fallback via
    # WORKSPACE_TEST_SQLITE=1 (see app.db).
    database_url: str = "postgresql://postgres:postgres@localhost:5432/workspace"

    # Supabase (optional — only used if the service reaches for the
    # Supabase-managed REST layer; direct DB access is via DATABASE_URL).
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    # Clerk
    verify_clerk: bool = False
    clerk_secret_key: str = ""
    clerk_jwks_url: str = ""
    clerk_issuer: str = ""

    # Machine tokens
    workspace_machine_token_signing_key: str = "change-me"

    # Feature flags
    auth_enabled: bool = True

    # Realtime
    sse_heartbeat_seconds: int = 15


@lru_cache
def get_settings() -> Settings:
    return Settings()
