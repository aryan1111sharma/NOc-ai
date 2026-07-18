# ChargeZone Jira — Priority Level (P1/P2/P3) fix (Design)

**Date:** 2026-07-18
**Branch:** `feat/mcp-server`
**Extends/fixes:** `chargezone-jira-mcp-server` (v0.7.0).
**Status:** Design — user-confirmed root cause + target numbers; building.

## 1. The bug (confirmed)

Asked for QCH "P1/P2/P3 open ticket numbers", the NOC Engineer agent read Jira's **built-in
Priority** field (98% "Medium" — a useless default) and **invented** a Highest/High/Medium →
P1/P2/P3 mapping, reporting P1=1/P2=0/P3=128. Wrong.

ChargeZone tracks P1/P2/P3 in a **custom field**: **"Priority Level" = `customfield_10120`**
(select; values `"Priority Level - 01" / "- 02" / "- 03"`). Confirmed by the user's own Jira CSV
export (394699) and by the user directly. Correct numbers (open = `statusCategory != Done`, 124):
**P1=44, P2=52, P3=9** (105 triaged, 19 unassigned). Whole project (4016): P1=2040, P2=1554,
P3=288, 134 empty.

Root cause: our tools never read `customfield_10120`; the agent had nothing real to answer with
and hallucinated a mapping.

## 2. Confirmed field facts (live QCH)

| Field | ID | Value shape | Notes |
|---|---|---|---|
| Priority Level | `customfield_10120` | `{"value": "Priority Level - 01"}` | 01/02/03 → P1/P2/P3; ~94% of open populated |
| OCPP ID | `customfield_10110` | plain string `"CG0003"` | 100% populated — the charger id |
| Category | `customfield_10156` | `{"value": "..."}` | sparse |
| Hardware Failure Description | `customfield_10119` | plain string | sparse |

JQL filtering works: `cf[10120] = "Priority Level - 01"` (passes cleanly through `build_scoped_jql`).

## 3. The fix

### 3.1 `cz_jira_stats` — add `by_priority_level`
Fetch `customfield_10120` in the paginated sample; add to output:
```
"by_priority_level": {"P1": n, "P2": n, "P3": n, "Unassigned": n}
```
Map `"Priority Level - 01"→"P1"`, `- 02→"P2"`, `- 03→"P3"`, empty/None→`"Unassigned"`. Computed
on the same ≤2000 sample as the other breakdowns (exact for the confirmed open-ticket use case;
`truncated` already signals large sets). Keep the existing `by_priority` (built-in) too.

### 3.2 `cz_jira_search` — surface real fields per issue
Add `priority_level` (P1/P2/P3 from `customfield_10120`) and `ocpp_id` (`customfield_10110`) to
each result dict (via `_drop_empty`, so absent when unset). Fetch those two custom fields.

### 3.3 `cz_jira_export` — include the QCH columns the user's export has
Rebuild export from REST `/search/jql` (paginated, reuse stats-style loop) with a **curated column
set** so it matches the user's Jira export and includes Priority Level:
```
Issue key, Issue Type, Summary, Status, Priority Level, Priority, Assignee, Reporter,
OCPP ID, Category, Hardware Failure Description, Created, Updated
```
`all_fields=True` additionally appends: Description (ADF→text), Parent key, Due date. Return shape
unchanged: `{headers, rows, row_count, truncated}`. This drops the BOM-prone CSV-servlet path.
`limit` still caps rows (default 1000, clamped 1–5000); `truncated` when the cap is hit.

### 3.4 Agent instruction (NOC Engineer)
Add a QCH query guide. Conditions passed to `cz_jira_search`/`cz_jira_stats`/`cz_jira_export` are
the WHERE clause only — do NOT include `project = QCH` (auto-added) or `ORDER BY` (rejected;
results are always newest-updated first).

"QCH (Quench) JIRA QUERYING:
- PRIORITY: P1/P2/P3 live in the **Priority Level** field (`cf[10120]`), NOT built-in Priority
  (almost always 'Medium' — ignore it for P-levels). Values: `Priority Level - 01`=P1,
  `- 02`=P2, `- 03`=P3. NEVER map Highest/High/Medium to P1/P2/P3.
- For P1/P2/P3 counts: `cz_jira_stats` and read `by_priority_level`.
- 'Open' = `statusCategory != Done` (this excludes QA/Closed/Done; includes To Do/In Progress/HOLD).
- Common `conditions`:
  - Open P1: `cf[10120] = \"Priority Level - 01\" AND statusCategory != Done`
  - Open P1+P2: `cf[10120] IN (\"Priority Level - 01\",\"Priority Level - 02\") AND statusCategory != Done`
  - Unassigned open: `assignee IS EMPTY AND statusCategory != Done`
  - Created last 7 days: `created >= -7d`
  - Overdue open: `due < now() AND statusCategory != Done`
  - Bugs only: `issuetype = Bug`
  - Text: `summary ~ \"charger\"`
  - Aging open P1 (>30d): `cf[10120] = \"Priority Level - 01\" AND created <= -30d AND statusCategory != Done`
- To hand the user a file, call `cz_jira_export` then docgen `create_excel` and return the link."

## 4. Scope & safety
- All reads still route through `build_scoped_jql` / `require_qch` — QCH-locked, GET-only.
  `cf[10120] = "..."` conditions are balanced and ORDER-BY-free, so they pass the guard.
- No new endpoints beyond `/search/jql` (already used). No writes.

## 5. Constants
```
_PRIORITY_LEVEL_FIELD = "customfield_10120"
_OCPP_ID_FIELD = "customfield_10110"
_CATEGORY_FIELD = "customfield_10156"
_HW_FAIL_FIELD = "customfield_10119"
_PLEVEL_MAP = {"Priority Level - 01": "P1", "Priority Level - 02": "P2", "Priority Level - 03": "P3"}
```

## 6. Testing (mocked httpx)
- `by_priority_level`: mock issues with mixed/absent `customfield_10120` → asserts P1/P2/P3/Unassigned tally; empty field → "Unassigned".
- `cz_jira_search`: asserts `priority_level` + `ocpp_id` surfaced; absent when unset.
- `cz_jira_export`: asserts curated headers incl. "Priority Level"/"OCPP ID", value extraction from the `{value:..}` dict and plain-string fields, pagination, truncation, `all_fields` extra columns.
- Reuse scope tests; `cf[10120]=...` conditions still QCH-scoped.
- Update registration test if any tool count changes (it does not — same 8 tools).

## 7. Success criteria
- `cz_jira_stats("statusCategory != Done")` → `by_priority_level` `{P1:44, P2:52, P3:9, Unassigned:19}` (live, ±drift).
- `cz_jira_search` shows `priority_level`/`ocpp_id`; `cz_jira_export` output includes a "Priority Level" column with P-values.
- Agent answers "QCH P1/P2/P3 open" from `by_priority_level` (44/52/9), never the invented mapping.
- QCH-locked + GET-only preserved; tests green.

## 8. Out of scope
- Exact whole-project P-level counts beyond the 2000 stats sample (use export for full dumps).
- Non-QCH projects; any write path.
