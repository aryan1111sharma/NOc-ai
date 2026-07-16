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
from .server import _get_json_via, _load_charger_map, _resolve_charger_id

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


async def load_fleet_status(client, charger_map: Optional[dict] = None) -> dict[str, dict]:
    """{qcharger_id: {charger_id, ping_ms, online}} for the whole fleet.

    Two fleet-wide calls, merged:
      * GetChargerDetailsForMap  -> Ping_Timestamp (liveness)
      * GetChargerByCustomerIDForDD -> the numeric ChargerID

    Both are needed. The map feed's own ChargerID is **0 on every row** (verified live
    2026-07-16) -- it simply doesn't expose the id -- so trusting it schedules every
    device command against a non-existent charger 0. `charger_map` is the server's
    shared cache; it is populated on first use and reused thereafter.

    `online` is ping recency, NOT StatusID: the live feed returns StatusID 0 even for
    healthy, actively-pinging chargers, so it cannot be used for this.
    """
    if charger_map is None:
        charger_map = {}
    if not charger_map:
        charger_map.update(await _load_charger_map(client))
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
            "charger_id": charger_map.get(str(qid).lower()),
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


def register_fleet_tools(mcp, client, charger_map: dict) -> None:
    """Register the firmware tools onto `mcp`, sharing the server's charger map."""

    @mcp.tool
    async def cms_get_charger_firmware(qcharger_id: str) -> dict:
        """Firmware/release version running on ONE charger (by QChargerID, e.g.
        "ab-sam-00585") in the old CMS — e.g. "release-1.2.65". Read-only: runs the
        device's `release` GET. The CMS does not store firmware, so this asks the
        charger live and takes a few seconds. For several chargers at once use
        cms_get_fleet_firmware. `status` is "ok" when `release` is populated; a
        charger that cannot be reached reports status "timeout" (device silent) or
        "cms_error" (CMS busy — retry) rather than failing."""
        cid = await _resolve_charger_id(client, qcharger_id, charger_map)
        sem = asyncio.Semaphore(1)
        row = await _query_one(client, sem, qcharger_id, cid, "release", None,
                               settings.device_poll_timeout)
        return {"qcharger_id": qcharger_id, "release": row["result"],
                "status": row["status"],
                **({"detail": row["detail"]} if "detail" in row else {}),
                "execution_id": row.get("execution_id"),
                "executed_at": row.get("executed_at")}

    @mcp.tool
    async def cms_get_fleet_firmware(
        search: Optional[str] = None,
        qcharger_ids: Optional[list[str]] = None,
        include_offline: bool = False,
        limit: int = 0,
    ) -> dict:
        """Firmware/release versions across MANY chargers in the old CMS, e.g. "what
        releases are running across Ador's chargers?". Give EITHER `search` (matches
        QChargerID or customer name, as cms_list_chargers) OR an explicit
        `qcharger_ids` list — an unfiltered whole-fleet sweep is not available, because
        the CMS stores no firmware and each charger must be asked live.

        Offline chargers (no recent ping) are skipped and named in
        skipped_offline_ids; pass include_offline=True to query them anyway. Returns a
        `rollup` of release -> count (failures counted as "unreachable") plus a
        `chargers` row per charger queried. `queried + skipped_offline +
        skipped_over_cap` always equals the number of chargers matched, so a partial
        answer is never mistaken for a complete one."""
        if bool(search) == bool(qcharger_ids):
            raise ValueError(
                "Give exactly one of `search` (e.g. a customer name or QChargerID "
                "prefix) or `qcharger_ids`. An unfiltered fleet sweep is not offered: "
                "every charger must be asked live.")
        cap = limit if limit and limit > 0 else settings.fleet_max_targets

        status = await load_fleet_status(client, charger_map)
        if qcharger_ids:
            matched = [str(x) for x in qcharger_ids]
            missing = [q for q in matched if q not in status]
            if missing:
                raise ValueError(f"Unknown QChargerID(s): {', '.join(missing[:5])}")
        else:
            s = search.lower()
            matched = sorted(q for q in status if s in q.lower())
            if not matched:
                raise ValueError(f"No chargers matched '{search}'.")

        offline_ids = ([] if include_offline
                       else [q for q in matched if not status[q]["online"]])
        eligible = [q for q in matched if q not in set(offline_ids)]
        targets_ids, over_cap = eligible[:cap], eligible[cap:]
        # A charger present on the map feed but absent from the id map cannot be
        # queried; report it rather than scheduling against a bogus id.
        targets = {q: status[q]["charger_id"] for q in targets_ids
                   if status[q]["charger_id"]}
        unresolved = [q for q in targets_ids if not status[q]["charger_id"]]

        rows = await fan_out(client, targets, "release")
        rows = rows + [{"qcharger_id": q, "result": None, "status": "error",
                        "detail": "no numeric ChargerID found for this QChargerID"}
                       for q in unresolved]

        rollup: dict[str, int] = {}
        for r in rows:
            key = r["result"] if r["status"] == "ok" else "unreachable"
            rollup[str(key)] = rollup.get(str(key), 0) + 1
        return {
            "rollup": rollup,
            "queried": len(rows),
            "skipped_offline": len(offline_ids),
            "skipped_offline_ids": offline_ids,
            "skipped_over_cap": len(over_cap),
            "chargers": [{"qcharger_id": r["qcharger_id"], "release": r["result"],
                          "status": r["status"]} for r in rows],
        }
