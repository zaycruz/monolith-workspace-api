"""FastAPI entry point for monolith-workspace-api.

Mirrors Fleet API's main.py structure: lifespan-managed DB, explicit router
registration, and a catch-all exception handler that serializes errors as
`{"error": ...}` to match the bridge contract.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import get_settings
from app.routers import (
    agents,
    channels,
    dms,
    health,
    internal,
    me,
    messages,
    stream,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("workspace")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    logger.info("monolith-workspace-api starting up")
    await db.init_db()
    yield
    logger.info("monolith-workspace-api shutting down")
    await db.close_db()


app = FastAPI(
    title="Monolith Workspace API",
    version="0.1.0",
    description=(
        "Workspace messaging for Monolith: channels, DMs, threads, reactions, "
        "agent-to-agent tool-call routing, SSE realtime. Implements the contract "
        "in packages/chat-bridge/contract/WORKSPACE_API.md."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Health", "description": "Liveness + readiness probes."},
        {"name": "Identity", "description": "Who am I."},
        {"name": "Channels", "description": "Channel list, create, membership."},
        {"name": "Messages", "description": "Send, read, react, inbox poll."},
        {"name": "DMs", "description": "Synthetic 1:1 DM channels."},
        {"name": "Agents", "description": "Cross-agent tool call routing."},
        {"name": "Stream", "description": "Realtime SSE."},
    ],
)


settings = get_settings()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc):  # noqa: ARG001
    body = exc.detail
    if isinstance(body, dict) and "error" in body:
        content = body
    elif isinstance(body, str):
        content = {"error": body}
    else:
        content = {"error": str(body)}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):  # noqa: ARG001
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation error",
            "code": "validation_error",
            "detail": {"errors": exc.errors()},
        },
    )


app.include_router(health.router)
app.include_router(me.router)
app.include_router(channels.router)
app.include_router(messages.router)
app.include_router(dms.router)
app.include_router(agents.router)
app.include_router(stream.router)
app.include_router(internal.router)


@app.get("/api/openapi.json", include_in_schema=False)
async def get_openapi_spec():
    return JSONResponse(app.openapi())


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
