"""Pull firmware from the NEW NOC — it stores firmware, so this is fast (no device calls).

Paginates list_chargers (200/page cap) and records firmware_version where present.
Caveat: firmware is sparsely populated (~11% of chargers) and uses a different scheme
from the old CMS (e.g. `ador-samsung-1-27-v1` vs `release-1.2.65`) — a blank here means
the NOC has no stored value, NOT that the charger lacks firmware.

Runs INSIDE the noc-mcp-server container.

    docker cp scripts/firmware-inventory/pull_noc.py noc-mcp-server:/tmp/pull_noc.py
    docker exec noc-mcp-server python /tmp/pull_noc.py
    docker cp noc-mcp-server:/tmp/noc_firmware.json ./noc_firmware.json
"""

from __future__ import annotations

import asyncio
import json
import os

from fastmcp import Client
from noc_mcp.server import build_server

OUT = os.environ.get("NOC_OUT", "/tmp/noc_firmware.json")


async def main():
    rows = []
    seen = set()
    async with Client(build_server()) as c:
        cursor = None
        while True:
            # This list paginates by `cursor` (a charger id), NOT offset -- an offset arg
            # is silently ignored and re-returns page 1, yielding duplicates past `total`.
            args = {"limit": 200}
            if cursor:
                args["cursor"] = cursor
            r = await c.call_tool("list_chargers_api_v1_chargers_get", args)
            d = r.data
            page = d.get("chargers") if isinstance(d, dict) else d
            if not page:
                break
            for x in page:
                cid = x.get("charger_id") or x.get("genone_q_charger_id")
                if cid in seen:
                    continue
                seen.add(cid)
                rows.append({
                    "charger_id": cid,
                    "customer": x.get("customer_name"),
                    "firmware_version": x.get("firmware_version"),
                    "model": x.get("model"),
                    "is_online": x.get("is_online"),
                    "serial": x.get("genone_serial_number"),
                })
            cursor = d.get("next_cursor") if isinstance(d, dict) else None
            if not cursor:
                break
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    have_fw = sum(1 for x in rows if x["firmware_version"])
    print(f"new-NOC chargers: {len(rows)} | firmware_version present: {have_fw} -> {OUT}",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
