"""Joins mongo_reader's ground-truth session/turn/tool/feedback data with
store.py's best-effort llm-router dispatch events, and shapes the result for
the admin API (app.py). This is the only module that performs the
agent_id + nearest-timestamp correlation described in
session/store.py:EventStore.find_nearest -- everywhere else either reads
Mongo only (ground truth) or ingests events only (no joining).

A TurnEvent is enrichment, never a requirement: every field this module
produces falls back to a null/empty value when no matching event exists, and
callers (app.py) must keep working with an empty EventStore.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import mongo_reader
from .store import EventStore, TurnEvent


@dataclass(frozen=True)
class ToolUsage:
    tool_call_id: str | None
    name: str
    has_output: bool


@dataclass(frozen=True)
class TurnTimeline:
    """One User request -> Tool Filter -> Provider/model -> fallback ->
    tool calls -> result -> feedback step, per the required Session detail
    timeline."""

    message_id: str
    timestamp: Any
    query_preview: str
    response_preview: str
    requested_model: str | None
    actual_target: str | None
    fallback_attempted: list[str]
    tool_filter: dict | None
    tools: list[ToolUsage]
    tokens: dict | None
    latency_ms: float | None
    status: str
    feedback: dict | None


@dataclass(frozen=True)
class SessionListEntry:
    conversation_id: str
    agent_id: str | None
    user_id: str | None
    turn_count: int
    total_tokens: int | None
    models: list[str]
    tools_used: list[str]
    feedback_summary: dict
    started_at: Any
    last_activity: Any
    status: str


@dataclass(frozen=True)
class SessionDetailView:
    conversation_id: str
    agent_id: str | None
    user_id: str | None
    title: str | None
    started_at: Any
    last_activity: Any
    timeline: list[TurnTimeline]


def _to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return None


def _merge_tokens(
    user_turn: mongo_reader.TurnRecord | None,
    assistant_turn: mongo_reader.TurnRecord,
    event: TurnEvent | None,
) -> dict | None:
    """Prefers the provider's own `usage` object (exact) relayed via the
    matched TurnEvent; falls back to LibreChat's own per-message
    tokenCount (estimated -- see this service's README "Known
    limitations") when no event correlates to this turn."""
    if event is not None and event.tokens:
        return {**event.tokens, "source": "provider_usage"}

    tokens: dict = {}
    if user_turn is not None and user_turn.token_count is not None:
        tokens["input_tokens_estimated"] = user_turn.token_count
    if assistant_turn.token_count is not None:
        tokens["output_tokens_estimated"] = assistant_turn.token_count
    if not tokens:
        return None
    tokens["source"] = "message_token_count"
    return tokens


def _turn_status(assistant_turn: mongo_reader.TurnRecord, event: TurnEvent | None) -> str:
    if event is not None and not event.ok:
        return "error"
    if assistant_turn.error:
        return "error"
    if assistant_turn.unfinished:
        return "unfinished"
    return "ok"


def _build_timeline(
    detail: mongo_reader.SessionDetail, event_store: EventStore, *, correlation_window_seconds: float
) -> list[TurnTimeline]:
    timeline: list[TurnTimeline] = []
    pending_user: mongo_reader.TurnRecord | None = None

    for turn in detail.turns:
        if turn.is_created_by_user:
            pending_user = turn
            continue

        event: TurnEvent | None = None
        agent_id = detail.summary.agent_id
        turn_iso = _to_iso(turn.created_at)
        if agent_id and turn_iso:
            event = event_store.find_nearest(
                agent_id, turn_iso, window_seconds=correlation_window_seconds
            )

        timeline.append(
            TurnTimeline(
                message_id=turn.message_id,
                timestamp=turn.created_at,
                query_preview=pending_user.text_preview if pending_user else "",
                response_preview=turn.text_preview,
                requested_model=turn.model,
                actual_target=event.target if event else None,
                fallback_attempted=list(event.attempted) if event else [],
                tool_filter=event.tool_filter if event else None,
                tools=[
                    ToolUsage(tool_call_id=t.tool_call_id, name=t.name, has_output=t.has_output)
                    for t in turn.tools
                ],
                tokens=_merge_tokens(pending_user, turn, event),
                latency_ms=event.latency_ms if event else None,
                status=_turn_status(turn, event),
                feedback=turn.feedback,
            )
        )
        pending_user = None

    return timeline


def build_session_detail(
    db: Any,
    event_store: EventStore,
    conversation_id: str,
    *,
    correlation_window_seconds: float,
) -> SessionDetailView | None:
    detail = mongo_reader.get_session_detail(db, conversation_id)
    if detail is None:
        return None

    timeline = _build_timeline(
        detail, event_store, correlation_window_seconds=correlation_window_seconds
    )
    last_activity = timeline[-1].timestamp if timeline else detail.summary.updated_at

    return SessionDetailView(
        conversation_id=detail.summary.conversation_id,
        agent_id=detail.summary.agent_id,
        user_id=detail.summary.user_id,
        title=detail.summary.title,
        started_at=detail.summary.created_at,
        last_activity=last_activity,
        timeline=timeline,
    )


def _summarize_list_entry(view: SessionDetailView) -> SessionListEntry:
    models: list[str] = []
    tools_used: list[str] = []
    total_tokens = 0
    has_tokens = False
    thumbs_up = 0
    thumbs_down = 0
    overall_status = "ok"

    for step in view.timeline:
        model_label = step.actual_target or step.requested_model
        if model_label and model_label not in models:
            models.append(model_label)
        for tool in step.tools:
            if tool.name not in tools_used:
                tools_used.append(tool.name)
        if step.tokens and "total_tokens" in step.tokens:
            total_tokens += step.tokens["total_tokens"]
            has_tokens = True
        elif step.tokens:
            total_tokens += sum(
                v for k, v in step.tokens.items() if k.endswith("_tokens_estimated")
            )
            has_tokens = True
        if step.feedback:
            rating = step.feedback.get("rating")
            if rating == "thumbsUp":
                thumbs_up += 1
            elif rating == "thumbsDown":
                thumbs_down += 1
        if step.status == "error":
            overall_status = "error"

    return SessionListEntry(
        conversation_id=view.conversation_id,
        agent_id=view.agent_id,
        user_id=view.user_id,
        turn_count=len(view.timeline),
        total_tokens=total_tokens if has_tokens else None,
        models=models,
        tools_used=tools_used,
        feedback_summary={"thumbs_up": thumbs_up, "thumbs_down": thumbs_down},
        started_at=view.started_at,
        last_activity=view.last_activity,
        status=overall_status,
    )


def build_session_list(
    db: Any,
    event_store: EventStore,
    *,
    agent_id: str | None = None,
    date_from: Any = None,
    date_to: Any = None,
    limit: int = 50,
    skip: int = 0,
    correlation_window_seconds: float,
) -> list[SessionListEntry]:
    summaries = mongo_reader.list_sessions(
        db, agent_id=agent_id, date_from=date_from, date_to=date_to, limit=limit, skip=skip
    )
    entries = []
    for summary in summaries:
        view = build_session_detail(
            db,
            event_store,
            summary.conversation_id,
            correlation_window_seconds=correlation_window_seconds,
        )
        if view is None:
            continue
        entries.append(_summarize_list_entry(view))
    return entries
