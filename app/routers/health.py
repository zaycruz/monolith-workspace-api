"""Liveness + readiness. Monitored by load balancers and deploy scripts."""

from __future__ import annotations

from fastapi import APIRouter

from app import db

router = APIRouter(tags=["Health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> dict:
    try:
        await db.fetchrow("SELECT 1 AS ok")
        return {"status": "ready"}
    except Exception as exc:  # pragma: no cover
        return {"status": "not_ready", "error": str(exc)}
