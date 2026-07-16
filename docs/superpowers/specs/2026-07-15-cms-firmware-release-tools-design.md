# CMS firmware/release tools — design

**Date:** 2026-07-15
**Status:** approved (design), not yet implemented
**Branch:** `feat/mcp-server`
**Related:** [`2026-07-14-cms-device-command-tools-design.md`](2026-07-14-cms-device-command-tools-design.md) (the
`cms_run_device_query` engine this builds on), `cms-mcp-server/docs/device-api-catalog.md`

## 1. Context

A NOC engineer triaging a charger needs to know what firmware it runs. Today that answer is
effectively unreachable:

- The old CMS **does not store firmware anywhere**. Its Actions page fetches it from each
  device at runtime, one charger at a time (confirmed by the user, 2026-07-15). There is no
  database column to read, so any fleet-wide answer is inherently N device round-trips.
- The only path is `cms_run_device_query(command="release")`. That works, but requires the
  agent to know the exact command string `release` out of a 129-command catalog. In practice
  it doesn't: asked for firmware, the assistant reported it had no such capability.
- `cms_get_charger_details` has no firmware field, so there is no fallback.

The device API's own spec (EcoG Open-API 2.0, captured from the device's `swagger.yml`)
documents the source of truth:

> **GET `/release`** — "returns application board FW release", `text/plain`, e.g. `1.x.x`

It is ApiID 7 in the CMS catalog, verb GET, so it is already reachable through the existing
read-only path — verified live on 2026-07-15 against `ab-sam-04104` and `ab-int64-00244`,
both returning `release-1.2.65`.

The gap is **discoverability and scale**, not capability. This spec adds two dedicated,
obvious tools over the existing engine.

### 1.1 Prerequisite (done)

This design assumed a working `cms_run_device_query`. It was in fact broken: DataTables
actions were posted as `application/json`, which the legacy binder answers with `200` and an
**empty body** rather than an error, so result correlation never found anything and every
query timed out. Fixed in `302eaaa` and verified live. See [[cms-datatables-content-type]].
Without that fix these tools would return `timeout` for every charger.

## 2. Goals / non-goals

**Goals**

1. `cms_get_charger_firmware(qcharger_id)` — the obvious tool an agent finds when asked
   "what firmware is this charger on?"
2. `cms_get_fleet_firmware(...)` — "what releases are running across these chargers?",
   answered with a rollup plus per-charger detail.
3. A reusable fan-out engine, so later per-command tools (OCPP status, config) are a thin
   wrapper each — the user's stated "one by one" plan.
4. Fix two defects this work would otherwise inherit (§6).

**Non-goals**

- Sweeping all ~1081 chargers in one call (§7.1).
- Any write path. These tools are read-only; the gated write allow-list is untouched and
  unreachable from here.
- Firmware *upgrade*, scheduling, or release-management. Read-only reporting only.
- Caching / persistence of last-known firmware. Deferred (§9).

## 3. Live-verified facts this design rests on

| Fact | Evidence |
|---|---|
| `release` returns `release-1.2.65` | Live, exec 442936 / 442937, StatusID 2 (07-15) |
| Device latency is **highly variable** | 6.7s idle (07-15) but **44s under load** (07-16). Sizing the timeout off the idle figure was a mistake — see §8. |
| Fleet status needs **two calls, merged** | `GetChargerDetailsForMap` carries `Ping_Timestamp` but **`ChargerID: 0` on every row** (07-16). The numeric id must come from `GetChargerByCustomerIDForDD`. Trusting the map's id scheduled every command against charger 0. |
| `StatusID` is **unusable** as an online signal | Returned `0` on healthy, actively-pinging chargers |
| Online fleet ≈ 66% | 714 of 1081 chargers pinged within 15 min |
| Output is double-encoded | `ReadJsonLogFile` yields `'"release-1.2.65"'` (quotes included) |
| `ApiID` is per-charger | A charger's catalog may lack `release` → must resolve, not hard-code |

## 4. Tools

### 4.1 `cms_get_charger_firmware(qcharger_id: str) -> dict`

Firmware for ONE charger. Thin wrapper: resolve → `query_device(command="release")` →
unwrap → classify.

```json
{"qcharger_id": "ab-sam-04104", "release": "release-1.2.65", "status": "ok",
 "execution_id": 442936, "executed_at": "15/07/2026 08:13:04"}
```

On failure, `release` is `null` and `status` carries the reason (§5). The tool does **not**
raise for an unreachable charger — that is a normal, reportable outcome, not an error. It
raises only for an unknown `qcharger_id` (a caller mistake).

No online pre-check here: that would cost a fleet-wide `GetChargerDetailsForMap` call to
answer one question. A down charger simply surfaces as `timeout` after the poll budget. The
online filter exists in the fleet tool, where one map call is amortised over many targets.

### 4.2 `cms_get_fleet_firmware(...) -> dict`

```python
cms_get_fleet_firmware(
    search: str | None = None,          # matches QChargerID or customer, as cms_list_chargers
    qcharger_ids: list[str] | None = None,   # explicit target list
    include_offline: bool = False,      # if True, also query stale-ping chargers
    limit: int = 100,                   # cap on chargers actually queried
) -> dict
```

Exactly one of `search` / `qcharger_ids` is required — an unfiltered fleet sweep is not
offered (§7.1). Response:

```json
{"rollup": {"release-1.2.65": 12, "release-1.2.60": 3, "unreachable": 5},
 "queried": 20, "skipped_offline": 41, "skipped_over_cap": 0,
 "skipped_offline_ids": ["ab-sam-00700", "ab-sam-00701"],
 "chargers": [
   {"qcharger_id": "ab-sam-00585", "release": "release-1.2.65", "status": "ok"},
   {"qcharger_id": "ab-sam-00591", "release": null, "status": "timeout"}]}
```

**Scope of each field (exactly one meaning, no overlap):**

- `chargers[]` lists **only chargers actually queried** — one row each, `len == queried`.
  Offline chargers are skipped before any device call and are therefore *not* rows here.
- `rollup` counts **only queried chargers**: one bucket per release value, plus
  `unreachable` for every queried-but-failed charger. `sum(rollup.values()) == queried`,
  always.
- `skipped_offline` / `skipped_offline_ids` — chargers filtered out by the online check.
  The ids are listed (cheap: strings only) so a skipped charger is never invisible; an
  engineer can see *which* are down, not just how many.
- `skipped_over_cap` — targets beyond `limit`, reported so a truncated answer can never be
  mistaken for a complete one.

The invariant: `queried + skipped_offline + skipped_over_cap == total targets matched`. A
caller can always reconcile the numbers, and "we didn't ask" is never presentable as "it has
no firmware".

## 5. Status taxonomy

Per-charger `status` is explicit and closed:

Per-charger `status` is explicit and closed. It describes **a charger we actually queried** —
"offline" is not a status here, because offline chargers are skipped before any device call
and reported via `skipped_offline_ids` (§4.2). That separation is deliberate: *"we asked and
got nothing"* and *"we never asked"* are different facts and must never share a bucket.

| Status | Meaning | Engineer action |
|---|---|---|
| `ok` | Device answered; `release` populated | — |
| `timeout` | Scheduled, no result within the budget | Device accepted but did not answer; suspect device |
| `cms_error` | CMS flapped/unavailable while querying | **Retry** — says nothing about the charger |
| `unsupported` | This charger's catalog has no `release` | Different model/firmware; check catalog |
| `error` | Anything else (message included) | Investigate |

Separating `cms_error` from `timeout` is the point. Conflating them is exactly the
misdiagnosis that cost hours on 2026-07-15 (§6.1): a flapping CMS was reported as "the
charger may be offline", aiming the engineer at healthy hardware.

## 6. Defects fixed as part of this work

Both are in code this feature builds directly on; leaving them would bake the bugs in.

### 6.1 A CMS flap is indistinguishable from "no result" (Important)

`device_commands._history_rows` wraps its poll in a bare `except Exception: return []`. A
`CmsUnavailableError` (flap) is therefore silently identical to "the device hasn't answered
yet", and surfaces to the user as *"the charger may be offline"* — pointing the engineer at
the wrong system. Observed live: an identical query failed, then succeeded unchanged.

**Fix:** let `_history_rows` distinguish transient CMS failure from an empty result, so
`_await_result` and the engine can classify `cms_error` vs `timeout`. Scope: minimal, at the
source, with a regression test.

### 6.2 Device output is double-encoded (Minor)

`ReadJsonLogFile` returns `'"release-1.2.65"'`. Add `_unwrap()` in the engine: if the parsed
result is a string that is itself a JSON scalar, decode once more; strip whitespace. So
`release` reads `release-1.2.65`. Applied in the firmware wrappers, not inside
`query_device` — raw output stays raw for other commands.

## 7. Engine

New module `cms_mcp/fleet.py` — fan-out engine plus both tools, registered via
`register_fleet_tools(mcp, client, charger_map)` from `build_server`, mirroring
`register_device_tools`. `device_commands.py` is already ~300 lines and single-purpose (the
gated write path); fan-out belongs beside it, not inside it.

One surgical extraction: the schedule→poll→read core of `cms_run_device_query` becomes
`query_device(client, cid, command, args, *, timeout) -> dict` in `device_commands.py`, and
the existing tool becomes a caller. Behaviour-preserving; existing tests must stay green
unchanged.

**Flow**

1. `load_fleet_status(client)` — ONE `GetChargerDetailsForMap` call → `{qcharger_id:
   {charger_id, ping_ts, online}}`. `online` = `Ping_Timestamp` within
   `fleet_online_window_minutes`. **Not** `StatusID` (§3).
2. Resolve targets from `search` / `qcharger_ids`.
3. Unless `include_offline`, drop offline targets → `skipped_offline`.
4. Cap at `limit` → `skipped_over_cap`.
5. Fan out with an `asyncio.Semaphore(fleet_concurrency)`; each target runs `query_device`
   with a per-charger timeout and is classified per §5. One charger's failure never fails
   the batch.
6. Build the rollup.

The catalog is resolved per charger (`ApiID` is per-charger) and cached per call.

### 7.1 Why no unfiltered fleet sweep

~700 online chargers at 8 concurrent is **~1 hour** of sustained load on a legacy backend
that flaps under load — the failure mode we are already fighting. (The original estimate of
~10 min assumed the 6.7s idle latency; the live smoke measured 44s under load, so the true
cost is ~6× worse than designed.) Offering an unfiltered sweep invites an agent to call it
casually. Requiring a filter makes the cost a deliberate choice. A caller who truly wants
everything can page through customers.

The load appears partly self-inflicted: the backend seems to serialise device commands, so
fanning out queues them and inflates per-charger latency. `fleet_concurrency` may therefore
be closer to a throughput ceiling than a throttle. Worth measuring single-vs-concurrent
latency properly before raising it — raising it may make things slower, not faster.

## 8. Config (`config.py`)

| Setting | Default | Rationale |
|---|---|---|
| `fleet_concurrency` | 8 | Bounded load on a flapping legacy backend |
| `fleet_online_window_minutes` | 15 | Ping recency = online (§3) |
| `fleet_max_targets` | 100 | Cap per call; overflow reported, never dropped |

Per-charger timeout reuses `device_poll_timeout`, **raised 20s → 90s** during the live smoke
(§13). The 20s figure was sized off a 6.7s idle measurement; under real load a healthy
charger took **44s**, so 20s reported working chargers as `timeout` — the exact
misdiagnosis this design exists to prevent. This also fixes `cms_run_device_query`, which
shared the same too-short default.

## 9. Deferred

- **Caching** last-known firmware with a timestamp. Real value (repeat fleet questions are
  slow), real cost (storage, staleness policy). Revisit once usage is observed.
- **Other per-command tools** (`ocpp-client/connectionstatus`, `ocpp-client/config`,
  `outlets`) — each ~5 lines on this engine. Intentionally out of scope: ship one, learn,
  repeat.
- **Cert-visibility gap:** 34 device GETs (the whole TLS/PnC cert tree, `system/iostate`,
  `system/facts`, `powerblock/*`) have no CMS catalog entry, so Plug-and-Charge cert faults
  cannot be diagnosed via CMS at all. Needs confirming per charger model. Not this spec.

## 10. Testing

The fake CMS gains fleet-map rows with controllable `Ping_Timestamp`s. Tests:

1. Single-charger happy path → `release-1.2.65`, `status: ok`.
2. **Unquoting** — a double-encoded payload yields `release-1.2.65`, no quotes.
3. Rollup counts group correctly across mixed releases, and `sum(rollup) == queried` (§4.2).
4. Offline chargers are skipped, counted in `skipped_offline`, **and named in
   `skipped_offline_ids`**; `include_offline=True` queries them instead.
5. Cap: >`limit` targets → exactly `limit` queried, remainder in `skipped_over_cap`.
6. **`cms_error` ≠ `timeout`** — a flapping CMS classifies as `cms_error`, never as a device
   fault (regression for §6.1).
6b. The reconciliation invariant `queried + skipped_offline + skipped_over_cap == matched`
   holds across mixed offline/cap/failure cases.
7. `unsupported` when the charger's catalog lacks `release`.
8. One charger failing does not fail the batch.
9. Concurrency never exceeds `fleet_concurrency` (instrumented counter).
10. Neither tool ever calls `ExecuteApi` with a non-GET, and no write path is reachable.

Per [[cms-datatables-content-type]], the fake must keep enforcing per-endpoint content-type:
a permissive fake is what let the last bug ship green.

## 11. Security

Read-only. `release` is a GET; `query_device` already refuses non-GET verbs. No confirmation
token, no allow-list interaction — the 8-command write allow-list is untouched and
unreachable. Fan-out raises no new authorization surface (same credentials, same endpoints);
its only new risk is **load**, bounded by §8.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Fleet load destabilises a flapping CMS | Concurrency 8, cap 100, filter required, online-only default |
| Agent calls fleet tool for one charger | Docstrings steer single→`cms_get_charger_firmware` |
| Ping window wrong for some sites | Configurable; 15 min from live data |
| Live smoke still owed | Verify both tools on real chargers before release |

## 13. Cost

~2 weeks human / ~1 hour AI-assisted.
