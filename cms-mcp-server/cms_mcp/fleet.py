"""Fleet fan-out: ask one read-only device command across many chargers.

The old CMS stores no per-device state (firmware, config, ...) — its own UI asks each
charger at runtime, one at a time. Any fleet-wide answer is therefore N device
round-trips, which is why this module filters to online chargers first, caps the
target count, and bounds concurrency: the legacy backend flaps under load.

See docs/superpowers/specs/2026-07-15-cms-firmware-release-tools-design.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

from .auth import CmsUnavailableError
from .config import settings
from .device_commands import query_device
from .server import _get_json_via, _resolve_charger_id

_MAP_PATH = "/ChargerDetails/GetChargerDetailsForMap"
_DATE_RE = re.compile(r"/Date\((\d+)")


def _ping_ms(value: Any) -> Optional[int]:
    """Epoch-ms out of the legacy `/Date(1781076330550)/` stamp; None if absent."""
    m = _DATE_RE.search(str(value or ""))
    return int(m.group(1)) if m else None


def _unwrap(result: Any) -> Any:
    """Normalise a device payload for display.

    ReadJsonLogFile double-encodes text results: the file holds a JSON string, so a
    parse yields '"release-1.2.65"' — quotes and all. Decode one extra layer when the
    value is itself a JSON scalar, and trim. Non-string payloads pass through
    untouched. Applied by the firmware wrappers only; query_device keeps output raw.
    """
    if not isinstance(result, str):
        return result
    text = result.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        try:
            inner = json.loads(text)
        except ValueError:
            return text
        if isinstance(inner, str):
            return inner.strip()
    return text


async def load_fleet_status(client) -> dict[str, dict]:
    """ONE call -> {qcharger_id: {charger_id, ping_ms, online}} for the whole fleet.

    `online` is ping recency, NOT StatusID: the live feed returns StatusID 0 even for
    healthy, actively-pinging chargers, so it cannot be used for this.
    """
    rows = await _get_json_via(client, _MAP_PATH)
    window_ms = settings.fleet_online_window_minutes * 60_000
    now_ms = time.time() * 1000
    out: dict[str, dict] = {}
    for r in rows:
        qid = r.get("QChargerID")
        if not qid:
            continue
        ping = _ping_ms(r.get("Ping_Timestamp"))
        out[str(qid)] = {
            "charger_id": r.get("ChargerID"),
            "ping_ms": ping,
            "online": ping is not None and (now_ms - ping) <= window_ms,
        }
    return out
