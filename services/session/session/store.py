"""Best-effort turn-dispatch event log, ingested from llm-router.

Deliberately a JSON file on disk, not a second database: same precedent as
llm_router.store.Registry (see that module's docstring). This store is
short-lived correlation input for the admin Sessions view, never the system
of record for session/turn identity -- that is LibreChat's own Mongo (see
mongo_reader.py). If this file were lost entirely, the admin view would just
show fewer enriched fields (actual provider/fallback/tools-offered/latency);
every session/turn/tool/feedback fact sourced from Mongo would be unaffected.

A ring buffer, not an append-only log: capped at `max_events`, oldest
dropped first, so a busy deployment cannot grow this file unboundedly.
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STORE_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TurnEvent:
    """One llm-router dispatch attempt, emitted best-effort by
    llm_router.session_client after Router.dispatch() resolves (success or
    AllTargetsFailedError) -- see that module's docstring for the emit side.

    `agent_id` + `recorded_at` are the only correlation key available to
    join this against a LibreChat Mongo turn (see aggregate.py); there is no
    shared conversationId/messageId because llm-router's request body never
    carries one (it is a stateless OpenAI-compatible surface -- see
    llm_router/proxy.py's module docstring).
    """

    agent_id: str
    #: "Kimi/k3-256k", or None when every target failed.
    target: str | None
    #: Full priority-ordered fallback chain actually tried.
    attempted: tuple[str, ...]
    status_code: int | None
    ok: bool
    #: {"prompt_tokens","completion_tokens","total_tokens"} from the
    #: provider's own `usage` object, never estimated -- None when absent
    #: (streaming response, or a malformed/missing usage object).
    tokens: dict | None
    latency_ms: float | None
    #: tool-filter-proxy's FilterDecision, relayed via the
    #: X-Tool-Filter-Decision request header -- {"applied","reason",
    #: "tools_before","tools_after","pinned","fallback_tier"}, or None if
    #: the request never passed through tool-filter-proxy (or it did not
    #: attach the header, e.g. an older deployment).
    tool_filter: dict | None
    error: str | None
    recorded_at: str = field(default_factory=_now)

    def to_json(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "target": self.target,
            "attempted": list(self.attempted),
            "status_code": self.status_code,
            "ok": self.ok,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "tool_filter": self.tool_filter,
            "error": self.error,
            "recorded_at": self.recorded_at,
        }

    @staticmethod
    def from_json(data: dict) -> "TurnEvent":
        return TurnEvent(
            agent_id=data["agent_id"],
            target=data.get("target"),
            attempted=tuple(data.get("attempted", [])),
            status_code=data.get("status_code"),
            ok=bool(data.get("ok", False)),
            tokens=data.get("tokens"),
            latency_ms=data.get("latency_ms"),
            tool_filter=data.get("tool_filter"),
            error=data.get("error"),
            recorded_at=data.get("recorded_at", _now()),
        )


class EventStore:
    """Loads/saves the ring buffer of TurnEvents. Every mutation goes
    through the same lock + atomic-rewrite pattern as
    llm_router.store.Registry, for the same reason: a crash mid-write must
    never leave a truncated/partial file behind."""

    def __init__(self, path: str | Path, *, max_events: int = 2000):
        self._path = Path(path)
        self._max_events = max_events
        self._lock = threading.Lock()
        if not self._path.exists():
            self._save([])

    def _load(self) -> list[dict]:
        if not self._path.exists():
            return []
        import json

        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return raw.get("events", [])

    def _save(self, events: list[dict]) -> None:
        import json

        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "$comment": (
                "NOC Session Intelligence: best-effort turn-dispatch events "
                "ingested from llm-router. Correlation input only -- see "
                "services/session/session/store.py. Never hand-edited."
            ),
            "version": STORE_VERSION,
            "events": events,
        }
        fd, tmp_name = tempfile.mkstemp(dir=str(self._path.parent), prefix=".session-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_name, self._path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def record(self, event: TurnEvent) -> None:
        with self._lock:
            events = self._load()
            events.append(event.to_json())
            if len(events) > self._max_events:
                events = events[-self._max_events :]
            self._save(events)

    def list_events(self, *, agent_id: str | None = None) -> list[TurnEvent]:
        events = [TurnEvent.from_json(e) for e in self._load()]
        if agent_id is not None:
            events = [e for e in events if e.agent_id == agent_id]
        return events

    def find_nearest(
        self, agent_id: str, near_iso: str, *, window_seconds: float
    ) -> TurnEvent | None:
        """The ingested event for `agent_id` whose recorded_at is closest to
        `near_iso`, within `window_seconds` either side -- best-effort
        correlation only, see this module's docstring. None if no event is
        close enough, so a caller never silently attaches the wrong turn's
        data to another turn."""
        try:
            target_dt = datetime.fromisoformat(near_iso)
        except (TypeError, ValueError):
            return None
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)

        best: TurnEvent | None = None
        best_delta: float | None = None
        for event in self.list_events(agent_id=agent_id):
            try:
                event_dt = datetime.fromisoformat(event.recorded_at)
            except (TypeError, ValueError):
                continue
            if event_dt.tzinfo is None:
                event_dt = event_dt.replace(tzinfo=timezone.utc)
            delta = abs((event_dt - target_dt).total_seconds())
            if delta > window_seconds:
                continue
            if best_delta is None or delta < best_delta:
                best, best_delta = event, delta
        return best
