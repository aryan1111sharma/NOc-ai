"""Freshness envelope for read tools.

Every curated read tool stamps its response with where the data came from and how old it is,
so the agent never has to infer freshness -- reporting archived data as current state is the
field-diagnosis failure this exists to prevent.

``live_or_history`` is the fallback primitive: try the live device, and on failure fall back to
the NOC backend's TimescaleDB-backed history, marking the result accordingly. It never raises;
callers get a structured payload either way, matching ``control_center._proxy_get``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

LIVE = "live"
HISTORY = "history"

# Payload keys inspected, in order, to date a historical record.
_AS_OF_KEYS = ("as_of", "cached_at", "timestamp", "updated_at", "created_at")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Attach UTC to a naive datetime so arithmetic never raises.

    DB drivers (this module's TimescaleDB backend included) commonly hand back naive
    datetimes for untyped timestamp columns; treat those as UTC rather than blowing up.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse(value: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; returns None rather than raising on junk."""
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str):
        return None
    text = value
    if text and text[-1] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _as_utc(dt)


def extract_as_of(payload: Optional[dict]) -> Optional[datetime]:
    """Pull a timestamp out of a backend payload, or None if it carries none.

    Checks ``_AS_OF_KEYS`` in priority order and parses ISO-8601 strings only -- a numeric
    epoch value (common for a key literally named ``timestamp``) is treated as absent, not
    parsed, since ``datetime.fromisoformat`` cannot handle it and guessing units (seconds vs.
    milliseconds) is more likely to mislead than help.
    """
    if not isinstance(payload, dict):
        return None
    for key in _AS_OF_KEYS:
        dt = _parse(payload.get(key))
        if dt is not None:
            return dt
    return None


def is_error(payload: Any) -> bool:
    """True for the ``{"error": ...}`` shape used by _proxy_get and the backend helpers."""
    return isinstance(payload, dict) and "error" in payload


def stamp(payload: Optional[dict], *, source: str, as_of: Optional[datetime] = None,
          reason: Optional[str] = None) -> dict:
    """Wrap ``payload`` in the freshness envelope.

    Envelope keys win over payload keys; a colliding payload key is preserved by escalating
    the ``payload_`` prefix until a free name is found, so no value is ever silently dropped
    even if the payload already contains a ``payload_<key>`` entry.

    ``reason`` is intentionally discarded when ``source == LIVE`` -- a live reading needs no
    explanation for why it's live.

    ``stale_seconds`` is not clamped at zero: if ``as_of`` is in the future (charger RTC drift
    is a real failure mode in this domain), staleness goes negative and ``clock_skew`` is set
    True, rather than a skewed clock making stale data silently look fresh.
    """
    if source == LIVE:
        as_of_dt: Optional[datetime] = _as_utc(as_of) or _utcnow()
        stale: Optional[int] = 0
        skew = False
        reason = None
    else:
        as_of_dt = _as_utc(as_of)
        if as_of_dt is None:
            stale = None
            skew = False
        else:
            stale = int((_utcnow() - as_of_dt).total_seconds())
            skew = stale < 0

    envelope = {
        "source": source,
        "as_of": _iso(as_of_dt) if as_of_dt else None,
        "stale_seconds": stale,
        "reason": reason,
        "clock_skew": skew,
    }
    out = dict(envelope)
    for k, v in (payload or {}).items():
        key = k
        while key in out:
            key = f"payload_{key}"
        out[key] = v
    return out


async def live_or_history(
    live_call: Callable[[], Awaitable[dict]],
    history_call: Callable[[], Awaitable[dict]],
    *,
    reason: str,
) -> dict:
    """Try live; on a structured error fall back to history. Never raises."""
    live_out = await live_call()
    if not is_error(live_out):
        return stamp(live_out, source=LIVE)

    hist_out = await history_call()
    if is_error(hist_out):
        return {
            "error": "live and history both unavailable",
            "live_error": live_out.get("error"),
            "history_error": hist_out.get("error"),
        }
    return stamp(hist_out, source=HISTORY, as_of=extract_as_of(hist_out), reason=reason)
