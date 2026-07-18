# ChargeZone Jira Priority Level Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox syntax.

**Goal:** Make QCH tools read ChargeZone's real **Priority Level** field (P1/P2/P3) and add safe sorting, so the agent answers priority questions correctly and supports the team's daily JQL patterns.

**Architecture:** Modify existing tools in `chargezone-jira-mcp-server/cz_jira_mcp/server.py`. Add custom-field constants/helpers; extend `build_scoped_jql` with a validated `order_by`; enrich stats/search; rebuild export from REST (drop the CSV servlet + `_parse_csv`). All stays QCH-locked + GET-only.

**Spec:** `docs/superpowers/specs/2026-07-18-chargezone-jira-priority-level-fix.md`

**Run tests:** cwd `chargezone-jira-mcp-server/`, `E:\cjain\noc-mcp-agent\.venv\Scripts\python.exe -m pytest`.

**IMPORTANT:** Read the current `cz_jira_mcp/server.py` first — you are MODIFYING existing functions (`build_scoped_jql`, `_issue_summary`, `cz_jira_search`, `cz_jira_stats`, `cz_jira_export`) and REMOVING `_parse_csv`. Match current text exactly when editing.

---

## Task 1: Constants, helpers, and `order_by` in `build_scoped_jql` (TDD)

**Files:** Modify `cz_jira_mcp/server.py`; add tests to `tests/test_scope.py`.

- [ ] **Step 1: Add failing tests** to `tests/test_scope.py`:
```python
from cz_jira_mcp.server import _validate_order_by, _plevel, _field_str

def test_validate_order_by_default_and_whitelist():
    assert _validate_order_by("") == "ORDER BY updated DESC"
    assert _validate_order_by("created DESC") == "ORDER BY created DESC"
    assert _validate_order_by("cf[10120] ASC, assignee ASC") == "ORDER BY cf[10120] ASC, assignee ASC"
    assert _validate_order_by("ORDER BY updated DESC") == "ORDER BY updated DESC"  # strips leading kw

import pytest
@pytest.mark.parametrize("bad", ["updated); DROP", "a OR b", "created DESC) OR (1=1", "assignee ASC; evil"])
def test_validate_order_by_rejects_injection(bad):
    with pytest.raises(ValueError, match="order_by"):
        _validate_order_by(bad)

def test_build_scoped_jql_with_order_by():
    from cz_jira_mcp.server import build_scoped_jql
    assert build_scoped_jql("statusCategory != Done", order_by="cf[10120] ASC") == \
        'project = "QCH" AND (statusCategory != Done) ORDER BY cf[10120] ASC'
    assert build_scoped_jql("", order_by="assignee ASC") == 'project = "QCH" ORDER BY assignee ASC'

def test_plevel_and_field_str():
    assert _plevel({"customfield_10120": {"value": "Priority Level - 02"}}) == "P2"
    assert _plevel({"customfield_10120": None}) == "Unassigned"
    assert _plevel({}) == "Unassigned"
    assert _field_str({"customfield_10110": "CG0003"}, "customfield_10110") == "CG0003"
    assert _field_str({"x": {"value": "Cat"}}, "x") == "Cat"
    assert _field_str({}, "x") is None
```

- [ ] **Step 2: Run — expect ImportError.**  `python -m pytest tests/test_scope.py -k "order_by or plevel or field_str" -v`

- [ ] **Step 3: Implement** in `server.py`.
(a) Add constants near `_KEY_RE` (in the scope-guards section):
```python
_PRIORITY_LEVEL_FIELD = "customfield_10120"   # QCH "Priority Level" (P1/P2/P3)
_OCPP_ID_FIELD = "customfield_10110"           # QCH "OCPP ID" (charger id)
_CATEGORY_FIELD = "customfield_10156"          # QCH "Category"
_HW_FAIL_FIELD = "customfield_10119"           # QCH "Hardware Failure Description"
_PLEVEL_MAP = {"Priority Level - 01": "P1", "Priority Level - 02": "P2", "Priority Level - 03": "P3"}

_ORDER_FIELD = r'(?:"[^"]+"|cf\[[0-9]+\]|[A-Za-z][A-Za-z0-9_]*)'
_ORDER_RE = re.compile(
    rf'^{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?(?:\s*,\s*{_ORDER_FIELD}(?:\s+(?:ASC|DESC))?)*$',
    re.IGNORECASE,
)


def _validate_order_by(order_by: str) -> str:
    """Return a safe 'ORDER BY ...' clause. Empty -> default. Whitelist: comma-separated
    <field> [ASC|DESC], where field is an identifier, cf[NNN], or a quoted name. Rejects
    anything else (parens, operators, semicolons) so it can't widen scope."""
    ob = (order_by or "").strip()
    if not ob:
        return "ORDER BY updated DESC"
    ob = re.sub(r'^order\s+by\s+', '', ob, flags=re.IGNORECASE).strip()
    if not _ORDER_RE.match(ob):
        raise ValueError(
            "Rejected order_by: use comma-separated '<field> [ASC|DESC]', e.g. "
            "'cf[10120] ASC, assignee ASC'."
        )
    return f"ORDER BY {ob}"


def _plevel(fields: dict) -> str:
    """Map the QCH Priority Level field value to P1/P2/P3, or 'Unassigned'."""
    v = fields.get(_PRIORITY_LEVEL_FIELD)
    val = v.get("value") if isinstance(v, dict) else v
    return _PLEVEL_MAP.get(val, "Unassigned")


def _field_str(fields: dict, fid: str):
    """Read a custom field as a string: select fields -> .value/.name; else the raw value."""
    v = fields.get(fid)
    if isinstance(v, dict):
        return v.get("value") or v.get("name")
    return v
```
(b) Replace `build_scoped_jql` signature/body to accept `order_by`:
```python
def build_scoped_jql(conditions: str, order_by: str = "") -> str:
    """Return a project-locked JQL query. `conditions` (WHERE only, no ORDER BY) are wrapped in
    `project = "<KEY>" AND (...)`; ordering comes ONLY from the validated `order_by` (default
    most-recently-updated). Structurally impossible for a clause to escape the project filter."""
    proj = f'project = "{settings.project_key}"'
    order = _validate_order_by(order_by)
    conditions = (conditions or "").strip()
    if not conditions:
        return f"{proj} {order}"
    if _contains_order_by(conditions):
        raise ValueError(
            "Rejected search conditions: remove ORDER BY from conditions; use the order_by "
            "parameter instead. Pass only filter conditions."
        )
    if not _parens_balanced(conditions):
        raise ValueError(
            "Rejected search conditions: unbalanced parentheses. Pass a self-contained JQL "
            "filter fragment."
        )
    return f"{proj} AND ({conditions}) {order}"
```

- [ ] **Step 4: Run — expect PASS.** `python -m pytest tests/test_scope.py -v`
  (Existing scope tests still pass: default order unchanged; ORDER-BY-in-conditions still rejected.)

- [ ] **Step 5: Commit** `git commit -m "feat(cz-jira): custom-field helpers + safe order_by in build_scoped_jql"`

---

## Task 2: `cz_jira_stats` — add `by_priority_level` (TDD)

**Files:** Modify `server.py`; add test to `tests/test_analytics.py`.

- [ ] **Step 1: Failing test** in `tests/test_analytics.py`:
```python
@pytest.mark.asyncio
async def test_cz_jira_stats_by_priority_level(mock_client):
    def mk(k, pl):
        f = {"status": {"name": "To Do"}, "issuetype": {"name": "Task"},
             "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"},
             "created": "2026-07-10T10:00:00.000+0000"}
        if pl is not None:
            f["customfield_10120"] = {"value": pl}
        return {"key": k, "fields": f}
    issues = [mk("QCH-1","Priority Level - 01"), mk("QCH-2","Priority Level - 02"),
              mk("QCH-3","Priority Level - 02"), mk("QCH-4","Priority Level - 03"), mk("QCH-5", None)]
    def handler(request):
        assert "customfield_10120" in request.url.params.get("fields")
        return httpx.Response(200, json={"issues": issues})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_stats", {"conditions": "statusCategory != Done"})
    assert res.data["by_priority_level"] == {"P1": 1, "P2": 2, "P3": 1, "Unassigned": 1}
```

- [ ] **Step 2: Run — expect fail** (`by_priority_level` KeyError).

- [ ] **Step 3: Implement** — in `cz_jira_stats`:
  - Change `_STATS_FIELDS` constant to append the priority-level field:
    `_STATS_FIELDS = "status,issuetype,priority,assignee,created,customfield_10120"`
  - Add a `by_plevel` Counter alongside the others, and in the per-issue loop add:
    `by_plevel[_plevel(f)] += 1`
  - Add to the returned dict (place after `by_priority`):
    ```python
    "by_priority_level": {k: by_plevel[k] for k in ("P1","P2","P3","Unassigned") if by_plevel.get(k)},
    ```

- [ ] **Step 4: Run — expect PASS.** `python -m pytest tests/test_analytics.py -v`

- [ ] **Step 5: Commit** `git commit -m "feat(cz-jira): cz_jira_stats by_priority_level (real P1/P2/P3)"`

---

## Task 3: `cz_jira_search` — surface `priority_level` + `ocpp_id`, add `order_by` (TDD)

**Files:** Modify `server.py`; add test to `tests/test_tools.py` or `test_analytics.py`.

- [ ] **Step 1: Failing test** (add to `tests/test_analytics.py`):
```python
@pytest.mark.asyncio
async def test_cz_jira_search_surfaces_priority_level_and_ocpp_and_orders(mock_client):
    captured = {}
    def handler(request):
        captured["jql"] = request.url.params.get("jql")
        captured["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"issues": [{"key": "QCH-1", "fields": {
            "summary": "Offline", "status": {"name": "To Do"}, "issuetype": {"name": "Task"},
            "priority": {"name": "Medium"}, "updated": "2026-07-10T10:00:00.000+0000",
            "project": {"key": "QCH"}, "customfield_10120": {"value": "Priority Level - 01"},
            "customfield_10110": "CG0003"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_search", {"conditions": "statusCategory != Done",
                                                   "order_by": "cf[10120] ASC"})
    assert captured["jql"] == 'project = "QCH" AND (statusCategory != Done) ORDER BY cf[10120] ASC'
    assert "customfield_10120" in captured["fields"] and "customfield_10110" in captured["fields"]
    assert res.data[0]["priority_level"] == "P1"
    assert res.data[0]["ocpp_id"] == "CG0003"
```

- [ ] **Step 2: Run — expect fail.**

- [ ] **Step 3: Implement:**
  - Update `_SEARCH_FIELDS` (inside `_register_tools`) to append `,customfield_10120,customfield_10110`.
  - Update `_DETAIL_FIELDS` to append `,customfield_10120,customfield_10110` too (so get_issue also shows them).
  - In `_issue_summary`, add to the `_drop_empty({...})` dict:
    ```python
    "priority_level": (_PLEVEL_MAP.get((issue.get("fields", {}).get(_PRIORITY_LEVEL_FIELD) or {}).get("value"))
                       if isinstance(issue.get("fields", {}).get(_PRIORITY_LEVEL_FIELD), dict) else None),
    "ocpp_id": _field_str(issue.get("fields", {}) or {}, _OCPP_ID_FIELD),
    ```
    (These are dropped by `_drop_empty` when absent.)
  - Change `cz_jira_search` signature to `(conditions: str = "", limit: int = 50, order_by: str = "")`
    and its call to `build_scoped_jql(conditions, order_by)`. Update its docstring: mention
    `order_by` (e.g. `'cf[10120] ASC, assignee ASC'`) and that results now include
    `priority_level` (P1/P2/P3 from the Priority Level field) and `ocpp_id`.

- [ ] **Step 4: Run — expect PASS** (full suite too — `_issue_summary` change affects existing search/detail tests: they don't set the custom fields, so `priority_level`/`ocpp_id` are dropped and existing assertions still hold).
  `python -m pytest -v`

- [ ] **Step 5: Commit** `git commit -m "feat(cz-jira): search surfaces priority_level + ocpp_id; add order_by"`

---

## Task 4: `cz_jira_export` — REST rebuild with curated custom columns; remove `_parse_csv` (TDD)

**Files:** Modify `server.py`; replace export tests in `tests/test_analytics.py`; remove `_parse_csv` + its 3 tests.

- [ ] **Step 1: Replace the export tests** in `tests/test_analytics.py` — DELETE the old CSV-servlet-based export tests (`test_parse_csv_*`, `test_cz_jira_export_scopes_jql_and_parses`, `test_cz_jira_export_all_fields_variant_and_truncation`, `test_cz_jira_export_non_csv_raises`, `test_cz_jira_export_clamps_limit`) and ADD:
```python
@pytest.mark.asyncio
async def test_cz_jira_export_rest_curated_columns(mock_client):
    captured = {}
    def handler(request):
        captured["path"] = request.url.path
        captured["jql"] = request.url.params.get("jql")
        captured["fields"] = request.url.params.get("fields")
        return httpx.Response(200, json={"issues": [{"key": "QCH-1", "fields": {
            "issuetype": {"name": "Task"}, "summary": "Offline", "status": {"name": "To Do"},
            "priority": {"name": "Medium"}, "assignee": {"displayName": "Q"},
            "reporter": {"displayName": "R"}, "created": "c", "updated": "u",
            "customfield_10120": {"value": "Priority Level - 01"}, "customfield_10110": "CG0003",
            "customfield_10156": {"value": "Charger Software Issue"}, "customfield_10119": "NA"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"conditions": "statusCategory != Done", "limit": 500})
    assert captured["path"] == "/rest/api/3/search/jql"
    assert captured["jql"] == 'project = "QCH" AND (statusCategory != Done) ORDER BY updated DESC'
    h = res.data["headers"]
    assert h == ["Issue key","Issue Type","Summary","Status","Priority Level","Priority",
                 "Assignee","Reporter","OCPP ID","Category","Hardware Failure Description",
                 "Created","Updated"]
    row = res.data["rows"][0]
    assert row[0] == "QCH-1" and row[4] == "Priority Level - 01" and row[8] == "CG0003"
    assert res.data["row_count"] == 1 and res.data["truncated"] is False

@pytest.mark.asyncio
async def test_cz_jira_export_all_fields_adds_columns_and_clamps(mock_client):
    def handler(request):
        return httpx.Response(200, json={"issues": [{"key": "QCH-2", "fields": {
            "description": {"type":"doc","version":1,"content":[{"type":"paragraph",
              "content":[{"type":"text","text":"boom"}]}]},
            "parent": {"key": "QCH-1"}, "duedate": "2026-08-01"}}]})
    mcp = build_server(client=mock_client(handler))
    async with Client(mcp) as c:
        res = await c.call_tool("cz_jira_export", {"all_fields": True, "limit": 0})
    assert res.data["headers"][-3:] == ["Description","Parent key","Due date"]
    assert "boom" in res.data["rows"][0][-3]
```

- [ ] **Step 2: Run — expect fail** (old behavior / removed helper).

- [ ] **Step 3: Implement:**
  - DELETE the `_parse_csv` function (now unused).
  - Replace the whole `cz_jira_export` tool body with a REST build:
```python
    @mcp.tool
    async def cz_jira_export(conditions: str = "", all_fields: bool = False,
                             limit: int = 1000, order_by: str = "") -> dict:
        """Export Quench (QCH) Jira issues as tabular data for download. Pass JQL filter
        *conditions* (no ORDER BY — use order_by) and optional order_by ('<field> [ASC|DESC]').
        Returns {headers, rows, row_count, truncated} with columns: Issue key, Issue Type,
        Summary, Status, Priority Level (P1/P2/P3 source value), Priority, Assignee, Reporter,
        OCPP ID, Category, Hardware Failure Description, Created, Updated. all_fields=True also
        appends Description, Parent key, Due date. To give the user a file, pass headers+rows to
        docgen create_excel and return the link. Read-only; limit caps rows (default 1000)."""
        limit = max(1, min(int(limit), 5000))
        jql = build_scoped_jql(conditions, order_by)
        fetch = ("summary,status,issuetype,priority,assignee,reporter,created,updated,"
                 f"{_PRIORITY_LEVEL_FIELD},{_OCPP_ID_FIELD},{_CATEGORY_FIELD},{_HW_FAIL_FIELD}")
        if all_fields:
            fetch += ",description,parent,duedate"
        issues: list[dict] = []
        token = None
        while len(issues) < limit:
            params = {"jql": jql, "maxResults": min(100, limit - len(issues)), "fields": fetch}
            if token:
                params["nextPageToken"] = token
            data = await _get_json(client, "/rest/api/3/search/jql", params=params)
            batch = data.get("issues", [])
            issues.extend(batch)
            token = data.get("nextPageToken")
            if not token or not batch:
                break
        truncated = bool(token) and len(issues) >= limit
        issues = issues[:limit]

        headers = ["Issue key", "Issue Type", "Summary", "Status", "Priority Level", "Priority",
                   "Assignee", "Reporter", "OCPP ID", "Category", "Hardware Failure Description",
                   "Created", "Updated"]
        if all_fields:
            headers += ["Description", "Parent key", "Due date"]
        rows = []
        for i in issues:
            f = i.get("fields", {}) or {}
            pl = f.get(_PRIORITY_LEVEL_FIELD)
            row = [
                i.get("key"),
                (f.get("issuetype") or {}).get("name"),
                f.get("summary"),
                (f.get("status") or {}).get("name"),
                (pl.get("value") if isinstance(pl, dict) else pl),
                (f.get("priority") or {}).get("name"),
                (f.get("assignee") or {}).get("displayName"),
                (f.get("reporter") or {}).get("displayName"),
                _field_str(f, _OCPP_ID_FIELD),
                _field_str(f, _CATEGORY_FIELD),
                _field_str(f, _HW_FAIL_FIELD),
                f.get("created"),
                f.get("updated"),
            ]
            if all_fields:
                row += [
                    _adf_to_text(f.get("description")).strip(),
                    (f.get("parent") or {}).get("key"),
                    f.get("duedate"),
                ]
            rows.append(["" if c is None else c for c in row])
        return {"headers": headers, "rows": rows, "row_count": len(rows), "truncated": truncated}
```
  - Remove now-unused imports if `csv`/`io` are no longer referenced anywhere (check: `_parse_csv` was the only user). If so, delete `import csv` and `import io`.

- [ ] **Step 4: Run FULL suite — expect PASS.** `python -m pytest -v` (report count).

- [ ] **Step 5: Commit** `git commit -m "feat(cz-jira): rebuild export from REST with QCH custom columns; drop CSV servlet"`

---

## Task 5: README

- [ ] Update `README.md` export/stats/search bullets: search now returns `priority_level`+`ocpp_id`; stats returns `by_priority_level`; export has curated custom columns; all three accept `order_by`. Commit `git commit -m "docs(cz-jira): document priority_level, order_by, export columns"`.

---

## Task 6: Deploy + agent instruction + release (controller, in-session)

- [ ] Rebuild container: `docker compose --project-directory <repo>/chat -f <repo>/chat/docker-compose.yml up -d --build chargezone-jira-mcp-server`.
- [ ] Live smoke: `cz_jira_stats("statusCategory != Done")` → `by_priority_level` ≈ `{P1:44,P2:52,P3:9,Unassigned:19}`; `cz_jira_search("cf[10120] = \"Priority Level - 01\" AND statusCategory != Done", order_by="assignee ASC")` returns P1 issues with `priority_level`/`ocpp_id`; `cz_jira_export(...)` headers include "Priority Level"/"OCPP ID".
- [ ] Wire agent: export live DB (`export-agents.ps1`); patch NOC Engineer instruction (append the QCH querying guide from spec §3.4); restore byte-safe via PowerShell (docker cp + `mongosh --file`); `docker restart noc-librechat`; verify tools reload.
- [ ] Version bump minor (`bump-version.ps1 minor`) → v0.9.0; push branch + tag.

---

## Self-Review
- Spec coverage: §3.1 stats→T2; §3.2 search→T3; §3.3 export→T4; §3.4 instruction→T6; order_by (from user JQL guides)→T1/T3/T4. Covered.
- Placeholders: none. Type consistency: `_plevel`/`_field_str`/`_validate_order_by`/`_PLEVEL_MAP`/field-id constants used consistently; `build_scoped_jql(conditions, order_by)` new 2nd arg applied at every call site (search, stats, export); stats still calls `build_scoped_jql(conditions)` (order_by defaults ""). Registration test unchanged (still 8 tools).
- Safety: order_by validated by strict whitelist (no parens/operators) and appended outside the wrapper; conditions still reject ORDER BY + unbalanced parens → scope-escape stays closed; GET-only preserved (export now uses `_get_json`).
- Orphans: `_parse_csv` + its tests removed with the CSV-servlet path; `csv`/`io` imports removed if unused.
