"""Pydantic wire models. Shapes match contract/openapi.yaml exactly.

Field names, enum values, and required-vs-optional are driven by the contract.
If the contract changes, change these — not the other way around.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

# ── Identity ───────────────────────────────────────────────────────────────


class AgentIdentity(BaseModel):
    agent_id: UUID | str
    workspace_id: str
    display_name: str
    kind: Literal["agent", "user"]


# ── Channels ───────────────────────────────────────────────────────────────


class Channel(BaseModel):
    channel_id: str
    name: str
    kind: Literal["public", "private", "dm"]
    member_count: int | None = None
    last_message_at: str | None = None


class ChannelListResponse(BaseModel):
    channels: list[Channel]


class CreateChannelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    topic: str | None = None
    kind: Literal["public", "private", "dm"] = "public"


class AddMemberRequest(BaseModel):
    human_id: str | None = None
    agent_container_id: str | None = None


# ── Messages ───────────────────────────────────────────────────────────────


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1)
    mentions: list[str] = Field(default_factory=list)
    thread_parent_id: str | None = None
    idempotency_key: str
    # Optional enrichments beyond the contract — populated by agent senders.
    tool_calls: list[dict[str, Any]] | None = None


class SendMessageResponse(BaseModel):
    message_id: str
    timestamp: str


class Message(BaseModel):
    message_id: str
    channel_id: str
    sender: str
    content: str
    timestamp: str
    mentions: list[str] = Field(default_factory=list)
    thread_parent_id: str | None = None


class ReadChannelResponse(BaseModel):
    messages: list[Message]
    next_cursor: str | None = None


# ── Reactions ──────────────────────────────────────────────────────────────

# Contract openapi allows any symbol string. The spec prompt further narrows
# the DB-level symbols to a fixed set (✓,!,?,✗,↻,+). We accept the broader
# openapi shape at the wire, enforce the narrow set in services/messaging.py.
ALLOWED_REACTION_SYMBOLS = {"✓", "!", "?", "✗", "↻", "+"}


class AddReactionRequest(BaseModel):
    message_id: str
    symbol: str


class OkResponse(BaseModel):
    ok: bool


# ── Inbox ──────────────────────────────────────────────────────────────────


class InboxMessage(BaseModel):
    message_id: str
    channel_id: str
    sender: str
    content: str
    timestamp: str


class CheckMessagesResponse(BaseModel):
    new_messages: list[InboxMessage]
    unread_channel_ids: list[str]


# ── Cross-agent tool call ──────────────────────────────────────────────────


class CollabToolCallRequest(BaseModel):
    tool_name: str
    tool_args: dict[str, Any] = Field(default_factory=dict)
    caller_agent_id: str


class CollabToolCallResponse(BaseModel):
    result: Any | None = None
    error: str | None = None


class ToolCallResultRequest(BaseModel):
    """POSTed by the target bridge to report the result of a tool_call.invoked."""

    result: Any | None = None
    error: str | None = None


# ── SSE events ─────────────────────────────────────────────────────────────

# The contract treats this as a discriminated union. We keep per-type models
# mostly for documentation; the SSE layer serializes plain dicts for speed.


class MessageCreatedEvent(BaseModel):
    type: Literal["message.created"] = "message.created"
    channel_id: str
    message_id: str
    sender: str
    content: str
    timestamp: str
    mentions: list[str] = Field(default_factory=list)
    thread_parent_id: str | None = None


class ReactionAddedEvent(BaseModel):
    type: Literal["reaction.added"] = "reaction.added"
    channel_id: str
    message_id: str
    symbol: str
    sender: str


class MemberAddedEvent(BaseModel):
    type: Literal["member.added"] = "member.added"
    channel_id: str
    member_type: Literal["user", "agent"]
    member_id: str


class AgentTypingEvent(BaseModel):
    type: Literal["agent.typing"] = "agent.typing"
    channel_id: str
    agent_id: str


class ToolCallInvokedEvent(BaseModel):
    type: Literal["tool_call.invoked"] = "tool_call.invoked"
    target_agent_id: str
    caller_agent_id: str
    tool_name: str
    args: dict[str, Any]
    call_id: str


class ToolCallCompletedEvent(BaseModel):
    type: Literal["tool_call.completed"] = "tool_call.completed"
    call_id: str
    result: Any | None = None
    error: str | None = None
