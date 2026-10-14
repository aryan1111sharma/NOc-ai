"""Read-only access to LibreChat's own MongoDB: the system of record for
SESSION identity, TURN metadata, TOOLS actually invoked, and FEEDBACK.

Deliberately duck-typed: every function takes a `db` object exposing
`.conversations` and `.messages`, each a pymongo-Collection-shaped object
(`.find(filter, ...)`, `.find_one(filter)`). Production wires in a real
pymongo `Database`; tests wire in a small hand-built fake (see
tests/fakes.py), matching this repo's existing "Fake" convention (see
services/llm-router/tests/fakes.py:FakeProviders) rather than depending on
mongomock. Never writes to either collection.

Schema fields read here are exactly the ones confirmed against the live
Mongoose schemas in the running noc-librechat container (data-schemas
package) and real sample documents -- see this service's README
"Data sources" section. Nothing here is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolInvocation:
    tool_call_id: str | None
    name: str
    #: True only when the tool_call content part carries a non-empty
    #: `output`. agents.ts's ToolCall type has no explicit success/failure
    #: boolean (see this service's README "Known limitations"), so this is
    #: reported honestly as "completed with output" rather than a
    #: fabricated pass/fail.
    has_output: bool
    args: Any


@dataclass(frozen=True)
class TurnRecord:
    message_id: str
    parent_message_id: str | None
    is_created_by_user: bool
    sender: str | None
    model: str | None
    text_preview: str
    token_count: int | None
    created_at: Any
    finish_reason: str | None
    error: bool
    unfinished: bool
    feedback: dict | None
    tools: list[ToolInvocation]


@dataclass(frozen=True)
class SessionSummary:
    conversation_id: str
    user_id: str | None
    agent_id: str | None
    endpoint: str | None
    title: str | None
    created_at: Any
    updated_at: Any
    turn_count: int


@dataclass(frozen=True)
class SessionDetail:
    summary: SessionSummary
    turns: list[TurnRecord]


_TEXT_PREVIEW_LEN = 200


def _preview(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    text = text.strip()
    if len(text) <= _TEXT_PREVIEW_LEN:
        return text
    return text[:_TEXT_PREVIEW_LEN] + "..."


def _extract_tools(content: Any) -> list[ToolInvocation]:
    """content[] parts of type TOOL_CALL -> ToolInvocation, per the confirmed
    shape in data-provider/src/types/agents.ts (see this module's docstring)."""
    if not isinstance(content, list):
        return []
    tools: list[ToolInvocation] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "tool_call":
            continue
        call = part.get("tool_call") or {}
        tools.append(
            ToolInvocation(
                tool_call_id=call.get("id"),
                name=call.get("name", "unknown"),
                has_output=bool(call.get("output")),
                args=call.get("args"),
            )
        )
    return tools


def _turn_from_message(msg: dict) -> TurnRecord:
    return TurnRecord(
        message_id=msg.get("messageId", ""),
        parent_message_id=msg.get("parentMessageId"),
        is_created_by_user=bool(msg.get("isCreatedByUser", False)),
        sender=msg.get("sender"),
        model=msg.get("model"),
        text_preview=_preview(msg.get("text")),
        token_count=msg.get("tokenCount"),
        created_at=msg.get("createdAt"),
        finish_reason=msg.get("finish_reason"),
        error=bool(msg.get("error", False)),
        unfinished=bool(msg.get("unfinished", False)),
        feedback=msg.get("feedback"),
        tools=_extract_tools(msg.get("content")),
    )


def _summary_from_conversation(convo: dict, turn_count: int) -> SessionSummary:
    return SessionSummary(
        conversation_id=convo.get("conversationId", ""),
        user_id=convo.get("user"),
        agent_id=convo.get("agent_id"),
        endpoint=convo.get("endpoint"),
        title=convo.get("title"),
        created_at=convo.get("createdAt"),
        updated_at=convo.get("updatedAt"),
        turn_count=turn_count,
    )


def list_sessions(
    db: Any,
    *,
    agent_id: str | None = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: int = 50,
    skip: int = 0,
) -> list[SessionSummary]:
    """Newest-first page of conversations, each with its message count.
    Only reads -- see this module's docstring."""
    query: dict = {}
    if agent_id:
        query["agent_id"] = agent_id
    if date_from is not None or date_to is not None:
        created = {}
        if date_from is not None:
            created["$gte"] = date_from
        if date_to is not None:
            created["$lte"] = date_to
        query["createdAt"] = created

    cursor = db.conversations.find(query).sort("createdAt", -1).skip(skip).limit(limit)
    summaries = []
    for convo in cursor:
        turn_count = db.messages.count_documents(
            {"conversationId": convo.get("conversationId")}
        )
        summaries.append(_summary_from_conversation(convo, turn_count))
    return summaries


def get_session_detail(db: Any, conversation_id: str) -> SessionDetail | None:
    """Full turn timeline for one conversation, oldest-first. None if the
    conversation does not exist."""
    convo = db.conversations.find_one({"conversationId": conversation_id})
    if convo is None:
        return None

    messages = list(
        db.messages.find({"conversationId": conversation_id}).sort("createdAt", 1)
    )
    turns = [_turn_from_message(m) for m in messages]
    summary = _summary_from_conversation(convo, len(turns))
    return SessionDetail(summary=summary, turns=turns)
