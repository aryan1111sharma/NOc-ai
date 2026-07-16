"""Merge the old-CMS sweep and new-NOC pull into one Excel workbook.

Inputs (produced by sweep_cms.py / pull_noc.py):
  * cms_firmware.jsonl  — one JSON object per line: qcharger_id, release, status, ...
  * noc_firmware.json   — list of {charger_id, firmware_version, customer, ...}

Chargers are keyed by their id (old-CMS QChargerID == new-NOC charger_id for the
overlapping ab-int64-* fleet). A charger may appear in one system or both; the sheet
carries a column per system plus a `source` column, so a blank firmware is never
confused with "not present in that system".

Runs INSIDE the docgen-mcp-server container (it has openpyxl).

    docker cp cms_firmware.jsonl docgen-mcp-server:/tmp/cms_firmware.jsonl
    docker cp noc_firmware.json  docgen-mcp-server:/tmp/noc_firmware.json
    docker cp scripts/firmware-inventory/build_excel.py docgen-mcp-server:/tmp/build_excel.py
    docker exec docgen-mcp-server python /tmp/build_excel.py
    docker cp docgen-mcp-server:/tmp/firmware_inventory.xlsx ./firmware_inventory.xlsx
"""

from __future__ import annotations

import json
import os

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

CMS_IN = os.environ.get("CMS_IN", "/tmp/cms_firmware.jsonl")
NOC_IN = os.environ.get("NOC_IN", "/tmp/noc_firmware.json")
CMS_ROSTER = os.environ.get("CMS_ROSTER", "/tmp/cms_roster.json")
OUT = os.environ.get("XLSX_OUT", "/tmp/firmware_inventory.xlsx")


def _key(charger_id: str) -> str:
    """Merge key. The CMS map feed returns QChargerID upper-cased while the NOC returns
    it lower-cased, so the SAME charger appears under two casings; normalise so a charger
    in both systems merges into one 'both' row instead of splitting into two."""
    return str(charger_id).strip().lower()


def _load_cms():
    rows = {}
    if os.path.exists(CMS_IN):
        with open(CMS_IN, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                rows[_key(r["qcharger_id"])] = r
    return rows


def _load_noc():
    if os.path.exists(NOC_IN):
        with open(NOC_IN, encoding="utf-8") as fh:
            return {_key(r["charger_id"]): r
                    for r in json.load(fh) if r.get("charger_id")}
    return {}


def _apply_roster(cms):
    """Fold the full CMS roster in so offline chargers -- which can't be asked for firmware
    live -- are still LISTED (status 'offline', firmware blank) rather than silently absent.
    A charger already swept keeps its real result; this only fills the gaps."""
    if not os.path.exists(CMS_ROSTER):
        return
    for r in json.load(open(CMS_ROSTER, encoding="utf-8")):
        k = _key(r["qcharger_id"])
        if k in cms:
            continue
        cms[k] = {"qcharger_id": r["qcharger_id"], "release": None,
                  "status": "offline" if not r.get("online") else "not_swept"}


def main():
    cms, noc = _load_cms(), _load_noc()
    _apply_roster(cms)
    ids = sorted(set(cms) | set(noc))

    wb = Workbook()
    ws = wb.active
    ws.title = "Firmware inventory"
    headers = ["QChargerID", "Customer", "In old CMS", "CMS firmware (release)",
               "CMS read status", "In new NOC", "NOC firmware", "NOC model", "Source"]
    ws.append(headers)
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="1F4E78")
    white = Font(bold=True, color="FFFFFF")
    for cell in ws[1]:
        cell.font = white
        cell.fill = fill

    for cid in ids:
        c = cms.get(cid)
        n = noc.get(cid)
        in_cms, in_noc = c is not None, n is not None
        source = "both" if in_cms and in_noc else ("old CMS" if in_cms else "new NOC")
        display_id = (c or {}).get("qcharger_id") or (n or {}).get("charger_id") or cid
        ws.append([
            display_id,
            (n or {}).get("customer") or "",
            "yes" if in_cms else "no",
            (c or {}).get("release") or "",
            (c or {}).get("status") or "",
            "yes" if in_noc else "no",
            (n or {}).get("firmware_version") or "",
            (n or {}).get("model") or "",
            source,
        ])

    widths = [18, 18, 10, 22, 14, 10, 22, 16, 10]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"

    # Summary sheet
    s = wb.create_sheet("Summary")
    cms_ok = sum(1 for r in cms.values() if r.get("status") == "ok")
    cms_offline = sum(1 for r in cms.values() if r.get("status") == "offline")
    cms_timeout = sum(1 for r in cms.values() if r.get("status") == "timeout")
    noc_fw = sum(1 for r in noc.values() if r.get("firmware_version"))
    for row in [
        ["Metric", "Count"],
        ["Total chargers (union)", len(ids)],
        ["In old CMS (full roster)", len(cms)],
        ["  firmware read live OK", cms_ok],
        ["  offline — not asked (device down)", cms_offline],
        ["  online but no answer (timeout)", cms_timeout],
        ["In new NOC", len(noc)],
        ["  new-NOC firmware present", noc_fw],
        ["  new-NOC firmware blank (not stored)", len(noc) - noc_fw],
    ]:
        s.append(row)
    s["A1"].font = bold
    s["B1"].font = bold
    s.column_dimensions["A"].width = 46
    s.column_dimensions["B"].width = 10

    wb.save(OUT)
    print(f"wrote {OUT}: {len(ids)} chargers "
          f"(cms={len(cms)} ok={cms_ok}, noc={len(noc)} fw={noc_fw})", flush=True)


if __name__ == "__main__":
    main()
