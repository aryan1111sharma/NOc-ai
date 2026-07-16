"""Live firmware sweep of the OLD CMS fleet — resumable, checkpointed.

The legacy CMS stores no firmware; each charger must be asked live (~45s under load),
so a full sweep of ~1077 chargers is ~1.5 hours and WILL be interrupted (the backend
flaps). This writes one JSON line per charger to CHECKPOINT as results arrive, and skips
any QChargerID already present there on a re-run, so it resumes rather than restarting.

Runs INSIDE the cms-mcp-server container (reuses its configured credentials; never reads
.env). Concurrency is bounded by settings.fleet_concurrency (8) — a safety limit on a
backend that degrades under load, not a throughput knob.

    docker cp scripts/firmware-inventory/sweep_cms.py cms-mcp-server:/tmp/sweep_cms.py
    docker exec -e SWEEP_LIMIT=5 cms-mcp-server python /tmp/sweep_cms.py   # dry run
    docker exec cms-mcp-server python /tmp/sweep_cms.py                    # full sweep
    docker cp cms-mcp-server:/tmp/cms_firmware.jsonl ./cms_firmware.jsonl
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from cms_mcp.config import settings
from cms_mcp.device_commands import query_device
from cms_mcp.fleet import _unwrap, load_fleet_status
from cms_mcp.server import build_client
from cms_mcp.auth import CmsUnavailableError

CHECKPOINT = os.environ.get("SWEEP_CHECKPOINT", "/tmp/cms_firmware.jsonl")
LIMIT = int(os.environ.get("SWEEP_LIMIT", "0"))          # 0 = whole fleet
INCLUDE_OFFLINE = os.environ.get("SWEEP_INCLUDE_OFFLINE") == "1"


def _already_done() -> set[str]:
    """QChargerIDs with a result already checkpointed, for resume."""
    done: set[str] = set()
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        done.add(json.loads(line)["qcharger_id"])
                    except (ValueError, KeyError):
                        pass
    return done


async def _one(client, sem, qid, cid, out_lock, counters):
    """Query one charger's release and append the classified result to the checkpoint."""
    row = {"qcharger_id": qid, "charger_id": cid, "release": None, "status": "error"}
    async with sem:
        try:
            res = await query_device(client, cid, "release", None,
                                     timeout=settings.device_poll_timeout)
            row.update(release=_unwrap(res.get("result")), status="ok",
                       executed_at=res.get("executed_at"))
        except CmsUnavailableError:
            row["status"] = "cms_error"       # MUST precede RuntimeError (it subclasses it)
        except RuntimeError:
            row["status"] = "timeout"
        except ValueError as exc:
            row["status"] = "unsupported"
            row["detail"] = str(exc)
        except Exception as exc:              # noqa: BLE001 — one charger must not kill the sweep
            row["detail"] = f"{type(exc).__name__}: {exc}"
    async with out_lock:
        with open(CHECKPOINT, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        counters[row["status"]] = counters.get(row["status"], 0) + 1
        counters["_done"] += 1
        if counters["_done"] % 20 == 0 or counters["_done"] == counters["_total"]:
            el = time.time() - counters["_t0"]
            print(f"  {counters['_done']}/{counters['_total']} "
                  f"({el/60:.1f} min) "
                  f"ok={counters.get('ok',0)} timeout={counters.get('timeout',0)} "
                  f"cms_error={counters.get('cms_error',0)} "
                  f"unsupported={counters.get('unsupported',0)}", flush=True)


async def main():
    async with build_client() as client:
        status = await load_fleet_status(client, {})
        done = _already_done()
        targets = []
        skipped_offline = skipped_unresolved = 0
        for qid, v in sorted(status.items()):
            if qid in done:
                continue
            if not v.get("charger_id"):
                skipped_unresolved += 1
                continue
            if not INCLUDE_OFFLINE and not v.get("online"):
                skipped_offline += 1
                continue
            targets.append((qid, v["charger_id"]))
        if LIMIT:
            targets = targets[:LIMIT]

        print(f"fleet={len(status)} already_done={len(done)} "
              f"skipped_offline={skipped_offline} skipped_unresolved={skipped_unresolved} "
              f"to_query={len(targets)}", flush=True)
        if not targets:
            print("nothing to do.", flush=True)
            return

        sem = asyncio.Semaphore(settings.fleet_concurrency)
        out_lock = asyncio.Lock()
        counters = {"_done": 0, "_total": len(targets), "_t0": time.time()}
        await asyncio.gather(*(
            _one(client, sem, qid, cid, out_lock, counters) for qid, cid in targets))
        print(f"DONE in {(time.time()-counters['_t0'])/60:.1f} min -> {CHECKPOINT}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
