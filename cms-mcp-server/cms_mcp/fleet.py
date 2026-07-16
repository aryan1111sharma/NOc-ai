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


async def _query_one(client, sem: asyncio.Semaphore, qcharger_id: str,
                     charger_id: int, command: str, args: Optional[dict],
                     timeout: float) -> dict:
    """One charger's result, classified. Never raises: a batch must survive any
    single charger, and every failure mode is a reportable fact, not an error."""
    row: dict = {"qcharger_id": qcharger_id, "result": None, "status": "error"}
    async with sem:
        try:
            out = await query_device(client, charger_id, command, args, timeout=timeout)
            row.update(status="ok", result=_unwrap(out.get("result")),
                       execution_id=out.get("execution_id"),
                       executed_at=out.get("executed_at"))
        except CmsUnavailableError:
            # The CMS flapped. Says nothing about the charger — retryable.
            # MUST stay above `except RuntimeError`: CmsUnavailableError subclasses it,
            # so reversing these two silently reclassifies every flap as a device fault.
            row["status"] = "cms_error"
        except RuntimeError:
            # Scheduled, but the device never answered within the budget.
            row["status"] = "timeout"
        except ValueError as exc:
            # This charger's catalog has no such command (ApiID is per-charger).
            row["status"] = "unsupported"
            row["detail"] = str(exc)
        except Exception as exc:  # noqa: BLE001 - never let one charger fail the batch
            row["detail"] = f"{type(exc).__name__}: {exc}"
    return row


async def fan_out(client, targets: dict[str, int], command: str,
                  args: Optional[dict] = None, *,
                  timeout: Optional[float] = None) -> list[dict]:
    """Run `command` across {qcharger_id: charger_id}, bounded by fleet_concurrency.

    Returns one classified row per target, in the order given. Concurrency is bounded
    because the legacy CMS flaps under load — this is a safety limit, not a tuning knob.
    """
    sem = asyncio.Semaphore(settings.fleet_concurrency)
    budget = timeout if timeout is not None else settings.device_poll_timeout
    return list(await asyncio.gather(*(
        _query_one(client, sem, qid, cid, command, args, budget)
        for qid, cid in targets.items())))
