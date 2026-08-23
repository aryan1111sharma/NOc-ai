# KB Document Upload Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a NOC engineer add a Word/Excel/PDF/text document to the knowledge base from LibreChat chat, gated on a role the model cannot forge, indexed immediately and searchable by the NOC Engineer agent.

**Architecture:** LibreChat stores chat attachments in S3 (`quench-manthan-kb`, ap-south-1) and injects the caller's id/email/role into MCP request headers. `kb-mcp-server` gains three write tools that verify the role server-side, copy the object into a `contributed` corpus prefix, and run the **existing** parse → chunk → embed → upsert pipeline. The contributed corpus is a writable Docker volume mounted at `/knowledge/contributed`, nested inside the read-only `knowledge/` bind mount, so the existing folder-is-scope walker picks it up with no change. Qdrant stays local; the corpus in S3 is the durable asset.

**Tech Stack:** Python 3.12, FastMCP 3.4.7, Qdrant, fastembed (BAAI/bge-base-en-v1.5), boto3, python-docx, openpyxl, pytest (in-memory Qdrant + fake embedder), Docker Compose, LibreChat v0.8.x.

**Spec:** `docs/superpowers/specs/2026-08-23-kb-document-upload-design.md`

**Assumptions carried from the spec** (change these in Task 10 config if you disagree): contributor role is `kb-contributor`; `ADMIN` also counts as a contributor; upload cap is 25 MB.

**Where to run tests:** all `pytest` commands run from `services/mcp/kb/`. Install deps once with `pip install -r requirements.txt`. The suite must never need a Qdrant container or a model download — `fastembed` must not be imported at module scope, and new modules must not construct boto3 clients at import time.

---

### Task 1: Verify how LibreChat handles a non-image attachment (SPIKE — no code)

This is Risk 1 in the spec and it gates everything after Task 2. If LibreChat consumes the attachment for text extraction instead of leaving an object in storage, the *arrival path* changes (Task 8 reads a watched `inbox/` prefix instead of `chat-uploads/`) while the permission model, scope, tools and tests stay identical.

**Files:** none — investigation only.

- [ ] **Step 1: Confirm the current file strategy and where objects land**

Run:
```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval 'db.files.find({},{_id:0,filename:1,filepath:1,source:1,type:1,context:1}).sort({createdAt:-1}).limit(5).forEach(f=>print(JSON.stringify(f)))'
```
Expected: rows with `"source":"local"` and `filepath` under `/uploads/<userId>/...` or `/images/<userId>/...`.

- [ ] **Step 2: Attach a .docx in chat and observe**

In LibreChat, open a chat with **NOC Engineer (Kimi)**, attach any small `.docx`, and send a message. Then:
```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval 'db.files.find({}).sort({createdAt:-1}).limit(1).forEach(f=>print(JSON.stringify({filename:f.filename,filepath:f.filepath,source:f.source,type:f.type,context:f.context,bytes:f.bytes})))'
docker exec noc-librechat sh -c 'ls -la /app/uploads/ 2>/dev/null; find /app/uploads -type f 2>/dev/null | head'
```

- [ ] **Step 3: Record the verdict in the spec**

Write one of these two lines into `docs/superpowers/specs/2026-08-23-kb-document-upload-design.md` under §8 Risk 1, replacing "unverified":

- **PASS** — "Verified 2026-08-23: a `.docx` attachment persists as a file object with a `files` row; `kb_upload_document` reads it from the `chat-uploads/` prefix as designed."
- **FAIL** — "Verified 2026-08-23: LibreChat consumes non-image attachments for text extraction and leaves no retrievable object. Arrival path changes to a watched `inbox/<userId>/` prefix; see Task 8 fallback."

- [ ] **Step 4: Commit the verdict**

```bash
git add docs/superpowers/specs/2026-08-23-kb-document-upload-design.md
git commit -m "docs(kb): record verified LibreChat attachment behaviour for upload risk 1"
```

**STOP.** If FAIL, tell the user before continuing — Task 8 changes shape.

---

### Task 2: Provision the S3 bucket and IAM service user

**This task creates real AWS resources and costs money.** Confirm with the user before running it. Account `775681068472`, region `ap-south-1`.

**Files:**
- Create: `docs/aws/kb-bucket-policy.json` (the IAM policy, kept in git for review)

- [ ] **Step 1: Create the bucket**

```bash
aws s3api create-bucket --bucket quench-manthan-kb --region ap-south-1 \
  --create-bucket-configuration LocationConstraint=ap-south-1
```
Expected: JSON with `"Location": "http://quench-manthan-kb.s3.amazonaws.com/"`.

- [ ] **Step 2: Block public access, enable versioning and encryption**

```bash
aws s3api put-public-access-block --bucket quench-manthan-kb \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket quench-manthan-kb \
  --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket quench-manthan-kb \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
```
Expected: no output (success).

Versioning matters here: `kb_remove_document` deletes objects, and a mistaken delete of a contributed document should be recoverable.

- [ ] **Step 3: Expire raw chat uploads after 90 days**

```bash
aws s3api put-bucket-lifecycle-configuration --bucket quench-manthan-kb \
  --lifecycle-configuration '{"Rules":[{"ID":"expire-chat-uploads","Status":"Enabled","Filter":{"Prefix":"chat-uploads/"},"Expiration":{"Days":90}}]}'
```
Expected: no output. `knowledge/contributed/` is deliberately **not** expired — it is the corpus of record.

- [ ] **Step 4: Write the scoped IAM policy**

Create `docs/aws/kb-bucket-policy.json`:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ListOnlyOurPrefixes",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::quench-manthan-kb",
      "Condition": {
        "StringLike": {
          "s3:prefix": ["chat-uploads/*", "knowledge/contributed/*", "snapshots/*"]
        }
      }
    },
    {
      "Sid": "ObjectAccessOurPrefixes",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
      "Resource": [
        "arn:aws:s3:::quench-manthan-kb/chat-uploads/*",
        "arn:aws:s3:::quench-manthan-kb/knowledge/contributed/*",
        "arn:aws:s3:::quench-manthan-kb/snapshots/*"
      ]
    }
  ]
}
```

- [ ] **Step 5: Create the service user and attach the policy**

```bash
aws iam create-user --user-name manthan-kb-svc
aws iam put-user-policy --user-name manthan-kb-svc \
  --policy-name manthan-kb-access \
  --policy-document file://docs/aws/kb-bucket-policy.json
aws iam create-access-key --user-name manthan-kb-svc
```
Expected: the last command prints `AccessKeyId` and `SecretAccessKey` **once**.

**Do not paste the secret into chat, a commit, or a log.** Put it straight into `apps/ai-platform/librechat/.env` as `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` (that file is gitignored). If it does leak, rotate with `aws iam delete-access-key` + `create-access-key`.

- [ ] **Step 6: Verify the credentials work and are properly scoped**

```bash
AWS_ACCESS_KEY_ID=<new> AWS_SECRET_ACCESS_KEY=<new> aws s3 ls s3://quench-manthan-kb/knowledge/contributed/
AWS_ACCESS_KEY_ID=<new> AWS_SECRET_ACCESS_KEY=<new> aws s3 ls s3://quench-manthan-data/
```
Expected: first succeeds (empty listing). Second **fails** with AccessDenied — proving the key cannot reach the data-feed bucket.

- [ ] **Step 7: Commit the policy**

```bash
git add docs/aws/kb-bucket-policy.json
git commit -m "chore(aws): scoped IAM policy for the manthan-kb-svc service user"
```

---

### Task 3: Parse `.docx`

**Files:**
- Modify: `services/mcp/kb/kb_mcp/parse.py`
- Modify: `services/mcp/kb/requirements.txt`
- Test: `services/mcp/kb/tests/test_parse.py`

- [ ] **Step 1: Add the dependency**

Append to `services/mcp/kb/requirements.txt`:
```
python-docx>=1.1
```
Then: `pip install python-docx`

- [ ] **Step 2: Write the failing tests**

Append to `services/mcp/kb/tests/test_parse.py`:
```python
def _make_docx(path, paragraphs, table_rows=None, heading=None):
    from docx import Document
    doc = Document()
    if heading:
        doc.add_heading(heading, level=1)
    for p in paragraphs:
        doc.add_paragraph(p)
    if table_rows:
        t = doc.add_table(rows=len(table_rows), cols=len(table_rows[0]))
        for r, row in enumerate(table_rows):
            for c, val in enumerate(row):
                t.cell(r, c).text = val
    doc.save(path)


def test_parses_docx_paragraphs_and_tables(tmp_path: Path):
    f = tmp_path / "runbook.docx"
    _make_docx(f, ["Connector lock failure recovery."],
               table_rows=[["Code", "Meaning"], ["E42", "comms lost"]],
               heading="Stuck Connector")
    segs = parse_file(f)
    body = " ".join(s["text"] for s in segs)
    assert "Connector lock failure recovery." in body
    assert "E42" in body and "comms lost" in body
    assert any(s["location"] == "Stuck Connector" for s in segs)


def test_empty_docx_yields_no_segments(tmp_path: Path):
    f = tmp_path / "empty.docx"
    _make_docx(f, [])
    assert parse_file(f) == []


def test_docx_is_supported(tmp_path: Path):
    assert ".docx" in SUPPORTED
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_parse.py -v -k docx`
Expected: FAIL — `.docx` is not in `SUPPORTED`, so `parse_file` returns `[]`.

- [ ] **Step 4: Implement**

In `services/mcp/kb/kb_mcp/parse.py`, change the `SUPPORTED` set and add the parser:
```python
SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm"}


def _parse_docx(path: Path) -> list[dict]:
    """Paragraphs and table cells, tagged with the nearest preceding heading.

    Word has no pages until it is rendered, so `location` uses the document's
    own structure instead: a heading is the most useful thing to cite back.

    The body is walked in document order rather than through `doc.paragraphs`
    and `doc.tables`. Those are two independent collections, so iterating them
    separately loses the interleaving and tags every table with whichever
    heading happened to come last in the file — which would cite a fault table
    against the wrong procedure.
    """
    import docx  # imported lazily: a parser dependency must not slow server start
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    segs: list[dict] = []
    current = path.name

    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            if para.style is not None and (para.style.name or "").startswith("Heading"):
                current = text
                continue
            segs.append({"text": text, "location": current})
        elif child.tag.endswith("}tbl"):
            rows = []
            for row in Table(child, doc).rows:
                # A horizontally merged cell is repeated once per column it
                # spans; emitting it each time duplicates text into the
                # embedding, so keep only the first appearance.
                cells, previous = [], None
                for cell in row.cells:
                    if previous is not None and cell._tc is previous:
                        continue
                    previous = cell._tc
                    cells.append(cell.text.strip())
                if any(cells):
                    rows.append(" | ".join(cells))
            if rows:
                segs.append({"text": "\n".join(rows), "location": current})

    return segs
```

Add these two regression tests as well — the heading test above has only one heading,
so a correct and an incorrect implementation both pass it:
```python
def test_each_table_gets_the_heading_it_sits_under(tmp_path: Path):
    """Regression: tables were previously tagged with the last heading in the
    whole document, so a table under one section was cited as another."""
    from docx import Document

    doc = Document()
    doc.add_heading("Section A", level=1)
    t1 = doc.add_table(rows=1, cols=2)
    t1.cell(0, 0).text = "A-Code"
    t1.cell(0, 1).text = "value under A"
    doc.add_heading("Section B", level=1)
    t2 = doc.add_table(rows=1, cols=2)
    t2.cell(0, 0).text = "B-Code"
    t2.cell(0, 1).text = "value under B"
    f = tmp_path / "sections.docx"
    doc.save(f)

    segs = parse_file(f)
    by_text = {s["text"].split(" | ")[0]: s["location"] for s in segs}
    assert by_text["A-Code"] == "Section A"
    assert by_text["B-Code"] == "Section B"


def test_merged_cells_are_not_duplicated(tmp_path: Path):
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=1, cols=3)
    table.cell(0, 0).merge(table.cell(0, 1))
    table.cell(0, 0).text = "Merged Header"
    table.cell(0, 2).text = "Col3"
    f = tmp_path / "merged.docx"
    doc.save(f)

    segs = parse_file(f)
    assert segs[0]["text"] == "Merged Header | Col3"
```

**Known accepted gaps** (do not fix here): headers, footers, footnotes and tables nested
inside a table cell are silently dropped, because only the top-level body is walked.
Recorded so the limitation is known rather than discovered later.

Then wire it into `parse_file`, immediately after the existing `.pdf` branch:
```python
    if ext == ".docx":
        return _parse_docx(path)
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_parse.py -v`
Expected: PASS, including the pre-existing tests.

- [ ] **Step 6: Commit**

```bash
git add services/mcp/kb/kb_mcp/parse.py services/mcp/kb/tests/test_parse.py services/mcp/kb/requirements.txt
git commit -m "feat(kb): parse .docx documents into headed segments"
```

---

### Task 4: Parse `.xlsx` and `.csv`

Spreadsheet chunks must carry their header row: meaning lives in the headers, and a chunk of bare numbers embeds to a vector that matches nothing.

**Files:**
- Modify: `services/mcp/kb/kb_mcp/parse.py`
- Modify: `services/mcp/kb/requirements.txt`
- Test: `services/mcp/kb/tests/test_parse.py`

- [ ] **Step 1: Add the dependency**

Append to `services/mcp/kb/requirements.txt`:
```
openpyxl>=3.1
```
Then: `pip install openpyxl`

- [ ] **Step 2: Write the failing tests**

Append to `services/mcp/kb/tests/test_parse.py`:
```python
def test_parses_xlsx_one_segment_per_sheet(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    s1 = wb.active
    s1.title = "Faults"
    s1.append(["Code", "Meaning"])
    s1.append(["E42", "comms lost"])
    s2 = wb.create_sheet("Sites")
    s2.append(["Site", "City"])
    s2.append(["QNCH-1", "Pune"])
    f = tmp_path / "matrix.xlsx"
    wb.save(f)

    segs = parse_file(f)
    locations = {s["location"] for s in segs}
    assert locations == {"Faults", "Sites"}
    faults = next(s for s in segs if s["location"] == "Faults")
    assert "Code | Meaning" in faults["text"]
    assert "E42 | comms lost" in faults["text"]


def test_empty_xlsx_sheet_produces_no_segment(tmp_path: Path):
    from openpyxl import Workbook
    wb = Workbook()
    wb.active.title = "Blank"
    f = tmp_path / "blank.xlsx"
    wb.save(f)
    assert parse_file(f) == []


def test_parses_csv_and_repeats_header(tmp_path: Path):
    f = tmp_path / "tickets.csv"
    f.write_text("id,summary\nQCH-1,charger offline\nQCH-2,gun stuck\n", encoding="utf-8")
    segs = parse_file(f)
    assert len(segs) == 1
    assert segs[0]["location"] == "tickets.csv"
    assert "id | summary" in segs[0]["text"]
    assert "QCH-2 | gun stuck" in segs[0]["text"]


def test_spreadsheet_extensions_supported(tmp_path: Path):
    assert ".xlsx" in SUPPORTED and ".csv" in SUPPORTED
```

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_parse.py -v -k "xlsx or csv"`
Expected: FAIL — extensions unsupported, `parse_file` returns `[]`.

- [ ] **Step 4: Implement**

In `services/mcp/kb/kb_mcp/parse.py`, extend `SUPPORTED`:
```python
SUPPORTED = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".markdown", ".txt", ".html", ".htm"}
```

Add both parsers:
```python
def _rows_to_text(rows: list[list[str]]) -> str:
    """Pipe-join cells and keep the header on its own first line.

    The header is what makes a row of values mean anything, so it is retained
    verbatim; Task 5's chunker re-prepends it when a sheet spans chunks.
    """
    return "\n".join(" | ".join(cells) for cells in rows)


def _parse_xlsx(path: Path) -> list[dict]:
    from openpyxl import load_workbook  # lazy: keeps server start fast

    wb = load_workbook(str(path), read_only=True, data_only=True)
    try:
        segs = []
        for sheet in wb.worksheets:
            rows = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if v is None else str(v).strip() for v in row]
                if any(cells):
                    rows.append(cells)
            if rows:
                segs.append({"text": _rows_to_text(rows), "location": sheet.title})
        return segs
    finally:
        wb.close()


def _parse_csv(path: Path) -> list[dict]:
    import csv

    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        rows = [[c.strip() for c in row] for row in csv.reader(fh) if any(c.strip() for c in row)]
    return [{"text": _rows_to_text(rows), "location": path.name}] if rows else []
```

Wire both into `parse_file`, after the `.docx` branch:
```python
    if ext == ".xlsx":
        return _parse_xlsx(path)
    if ext == ".csv":
        return _parse_csv(path)
```

- [ ] **Step 5: Run to verify they pass**

Run: `python -m pytest tests/test_parse.py -v`
Expected: PASS (all parse tests, old and new).

- [ ] **Step 6: Commit**

```bash
git add services/mcp/kb/kb_mcp/parse.py services/mcp/kb/tests/test_parse.py services/mcp/kb/requirements.txt
git commit -m "feat(kb): parse .xlsx per sheet and .csv with header rows retained"
```

---

### Task 5: Keep the header row on every spreadsheet chunk

A sheet longer than `chunk_max_chars` splits, and every chunk after the first loses the header. The 200-character overlap does not reliably carry it.

**Files:**
- Modify: `services/mcp/kb/kb_mcp/chunk.py`
- Test: `services/mcp/kb/tests/test_chunk.py`

- [ ] **Step 1: Write the failing test**

Append to `services/mcp/kb/tests/test_chunk.py`:
```python
from kb_mcp.chunk import chunk_text, chunk_tabular


def test_tabular_chunks_all_carry_the_header():
    header = "id | summary"
    body = "\n".join(f"QCH-{i} | charger {i} offline at site" for i in range(400))
    chunks = chunk_tabular(f"{header}\n{body}", max_chars=800, overlap_chars=0)
    assert len(chunks) > 1
    assert all(c.startswith(header) for c in chunks)


def test_tabular_single_chunk_does_not_duplicate_header():
    text = "id | summary\nQCH-1 | offline"
    chunks = chunk_tabular(text, max_chars=800, overlap_chars=0)
    assert chunks == [text]


def test_tabular_empty_input_yields_nothing():
    assert chunk_tabular("", max_chars=800, overlap_chars=0) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_chunk.py -v -k tabular`
Expected: FAIL with `ImportError: cannot import name 'chunk_tabular'`.

- [ ] **Step 3: Implement**

Append to `services/mcp/kb/kb_mcp/chunk.py`:
```python
def chunk_tabular(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Chunk row-oriented text, repeating the header line on every chunk.

    A spreadsheet row means nothing without its column names: "E42 | 3 | 17"
    embeds to a vector that matches no plausible question. Repeating the
    header costs a few characters per chunk and makes every chunk independently
    interpretable.
    """
    text = (text or "").strip()
    if not text:
        return []
    lines = text.split("\n")
    header, rows = lines[0], lines[1:]
    if not rows:
        return [header]

    budget = max_chars - len(header) - 1
    if budget <= 0:  # pathological header longer than a whole chunk
        return chunk_text(text, max_chars, overlap_chars)

    chunks, cur = [], []
    size = 0
    for row in rows:
        if cur and size + len(row) + 1 > budget:
            chunks.append(header + "\n" + "\n".join(cur))
            cur, size = [], 0
        cur.append(row)
        size += len(row) + 1
    if cur:
        chunks.append(header + "\n" + "\n".join(cur))
    return chunks
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_chunk.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/mcp/kb/kb_mcp/chunk.py services/mcp/kb/tests/test_chunk.py
git commit -m "feat(kb): repeat header row on every chunk of tabular content"
```

---

### Task 6: Caller identity and the permission gate

This is the security boundary. It is pure — no network, no framework, no I/O — so it can be tested exhaustively.

**Files:**
- Create: `services/mcp/kb/kb_mcp/identity.py`
- Test: `services/mcp/kb/tests/test_identity.py`

- [ ] **Step 1: Write the failing tests**

Create `services/mcp/kb/tests/test_identity.py`:
```python
import base64

import pytest

from kb_mcp.identity import Caller, IdentityError, is_contributor, parse_caller

ROLES = ("kb-contributor", "ADMIN")


def test_parses_headers_into_a_caller():
    c = parse_caller({"X-User-Id": "u1", "X-User-Email": "a@b.com", "X-User-Role": "ADMIN"})
    assert c == Caller(user_id="u1", email="a@b.com", role="ADMIN")


def test_header_names_are_case_insensitive():
    c = parse_caller({"x-user-id": "u1", "X-USER-ROLE": "ADMIN"})
    assert c.user_id == "u1" and c.role == "ADMIN"


def test_decodes_librechat_b64_prefixed_values():
    encoded = "b64:" + base64.b64encode("nöc@quench.local".encode()).decode()
    assert parse_caller({"x-user-id": "u1", "x-user-email": encoded}).email == "nöc@quench.local"


def test_undecodable_b64_value_becomes_empty_not_garbage():
    assert parse_caller({"x-user-id": "u1", "x-user-email": "b64:!!!"}).email == ""


def test_missing_user_id_is_refused():
    with pytest.raises(IdentityError, match="X-User-Id"):
        parse_caller({"x-user-role": "ADMIN"})


def test_empty_headers_are_refused():
    # get_http_headers() returns {} when there is no HTTP request at all
    # (stdio transport, or a bug). That must never read as an implicit grant.
    with pytest.raises(IdentityError):
        parse_caller({})


def test_whitespace_only_user_id_is_refused():
    with pytest.raises(IdentityError):
        parse_caller({"x-user-id": "   ", "x-user-role": "ADMIN"})


def test_contributor_roles_are_allowed():
    assert is_contributor(Caller("u1", "a@b.com", "kb-contributor"), ROLES)
    assert is_contributor(Caller("u1", "a@b.com", "ADMIN"), ROLES)


def test_role_match_is_case_insensitive():
    assert is_contributor(Caller("u1", "a@b.com", "admin"), ROLES)
    assert is_contributor(Caller("u1", "a@b.com", "KB-Contributor"), ROLES)


def test_other_roles_are_denied():
    assert not is_contributor(Caller("u1", "a@b.com", "noc-engineer"), ROLES)
    assert not is_contributor(Caller("u1", "a@b.com", "USER"), ROLES)


def test_absent_role_is_denied():
    assert not is_contributor(Caller("u1", "a@b.com", ""), ROLES)


def test_empty_allow_list_denies_everyone():
    assert not is_contributor(Caller("u1", "a@b.com", "ADMIN"), ())


def test_blank_entries_in_allow_list_grant_nothing():
    # KB_CONTRIBUTOR_ROLES="" or a stray comma must not create a role that
    # matches a caller with no role header.
    assert not is_contributor(Caller("u1", "a@b.com", ""), ("", "kb-contributor"))
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_identity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb_mcp.identity'`.

- [ ] **Step 3: Implement**

Create `services/mcp/kb/kb_mcp/identity.py`:
```python
"""Caller identity from MCP request headers.

Pure by design: no network, no framework, no config. This is the module that
decides who may write to the knowledge base, so it must be exhaustively
testable without standing anything up.

LibreChat substitutes {{LIBRECHAT_USER_ID}}, {{LIBRECHAT_USER_EMAIL}} and
{{LIBRECHAT_USER_ROLE}} into these headers server-side, from the authenticated
session (see @librechat/api/src/utils/env.ts). The model never sees them and
has no channel to set them — which is the whole reason a chat-facing tool may
be trusted to write. Values that are not header-safe ASCII arrive base64-encoded
behind a `b64:` prefix, which is documented in that same module.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

USER_ID_HEADER = "x-user-id"
USER_EMAIL_HEADER = "x-user-email"
USER_ROLE_HEADER = "x-user-role"


class IdentityError(ValueError):
    """The request carries no usable caller identity."""


@dataclass(frozen=True)
class Caller:
    user_id: str
    email: str
    role: str


def _decode(value: str) -> str:
    if not value.startswith("b64:"):
        return value
    try:
        return base64.b64decode(value[4:], validate=True).decode("utf-8")
    except Exception:
        # A malformed value is not a partial identity — return nothing rather
        # than a mangled string that might match something downstream.
        return ""


def parse_caller(headers: dict[str, str]) -> Caller:
    lowered = {str(k).lower(): _decode(str(v or "").strip()).strip()
               for k, v in (headers or {}).items()}
    user_id = lowered.get(USER_ID_HEADER, "")
    if not user_id:
        raise IdentityError(
            "This request carries no caller identity. The KB server requires the "
            "X-User-Id header that LibreChat injects; without it the server cannot "
            "tell who is asking, and refuses rather than assuming. If you are seeing "
            "this in chat, the `kb` MCP server is missing its `headers:` block in "
            "librechat.yaml."
        )
    return Caller(
        user_id=user_id,
        email=lowered.get(USER_EMAIL_HEADER, ""),
        role=lowered.get(USER_ROLE_HEADER, ""),
    )


def is_contributor(caller: Caller, allowed_roles: tuple[str, ...]) -> bool:
    allowed = {r.casefold() for r in allowed_roles if r and r.strip()}
    role = caller.role.casefold().strip()
    return bool(role) and role in allowed
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_identity.py -v`
Expected: PASS — 13 tests.

- [ ] **Step 5: Commit**

```bash
git add services/mcp/kb/kb_mcp/identity.py services/mcp/kb/tests/test_identity.py
git commit -m "feat(kb): caller identity and contributor permission gate"
```

---

### Task 7: S3 corpus access

Every AWS call in this service lives here, so the ingest pipeline stays unaware of where bytes came from.

**Files:**
- Create: `services/mcp/kb/kb_mcp/s3.py`
- Modify: `services/mcp/kb/tests/fakes.py`
- Modify: `services/mcp/kb/requirements.txt`
- Test: `services/mcp/kb/tests/test_s3.py`

- [ ] **Step 1: Add the dependency**

Append to `services/mcp/kb/requirements.txt`:
```
boto3>=1.34
```
Then: `pip install boto3`

- [ ] **Step 2: Add a fake S3 client**

Append to `services/mcp/kb/tests/fakes.py`:
```python
from datetime import datetime, timedelta, timezone


class FakeS3:
    """In-memory stand-in for the five boto3 S3 calls kb_mcp.s3 makes."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.times: dict[str, datetime] = {}
        self._clock = datetime(2026, 8, 23, tzinfo=timezone.utc)

    def put(self, key: str, data: bytes, age_minutes: int = 0) -> None:
        """Test helper — seeds an object. Not part of the boto3 surface."""
        self.objects[key] = data
        self.times[key] = self._clock - timedelta(minutes=age_minutes)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [
                {"Key": k, "Size": len(self.objects[k]), "LastModified": self.times[k]}
                for k in keys
            ],
            "IsTruncated": False,
        }

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        payload = self.objects[Key]

        class _Body:
            @staticmethod
            def read():
                return payload

        return {"Body": _Body(), "ContentLength": len(payload)}

    def copy_object(self, Bucket, Key, CopySource):
        src = CopySource["Key"] if isinstance(CopySource, dict) else CopySource
        self.put(Key, self.objects[src])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        self.times.pop(Key, None)
```

- [ ] **Step 3: Write the failing tests**

Create `services/mcp/kb/tests/test_s3.py`:
```python
import pytest

from kb_mcp.s3 import CHAT_PREFIX, CORPUS_PREFIX, S3Corpus, UploadRef
from tests.fakes import FakeS3


def _corpus():
    s3 = FakeS3()
    return S3Corpus(s3, "test-bucket"), s3


def test_finds_a_users_upload_by_original_filename():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc-123__runbook.docx", b"data")
    ref = corpus.find_user_upload("u1", "runbook.docx")
    assert isinstance(ref, UploadRef)
    assert ref.key == f"{CHAT_PREFIX}/u1/abc-123__runbook.docx"
    assert ref.filename == "runbook.docx" and ref.size == 4


def test_filename_match_is_case_insensitive():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__Runbook.DOCX", b"data")
    assert corpus.find_user_upload("u1", "runbook.docx") is not None


def test_returns_the_newest_when_a_name_was_uploaded_twice():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/old__spec.pdf", b"old", age_minutes=60)
    s3.put(f"{CHAT_PREFIX}/u1/new__spec.pdf", b"new", age_minutes=1)
    assert corpus.find_user_upload("u1", "spec.pdf").key.endswith("new__spec.pdf")


def test_cannot_see_another_users_upload():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u2/abc__secret.docx", b"data")
    assert corpus.find_user_upload("u1", "secret.docx") is None


def test_missing_upload_returns_none():
    corpus, _ = _corpus()
    assert corpus.find_user_upload("u1", "nope.pdf") is None


def test_a_file_with_no_uuid_prefix_still_matches():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/plain.txt", b"x")
    assert corpus.find_user_upload("u1", "plain.txt") is not None


def test_copy_to_corpus_places_the_object_under_the_corpus_prefix():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__runbook.docx", b"data")
    ref = corpus.find_user_upload("u1", "runbook.docx")
    key = corpus.copy_to_corpus(ref)
    assert key == f"{CORPUS_PREFIX}/runbook.docx"
    assert s3.objects[key] == b"data"


def test_download_corpus_writes_files_to_a_directory(tmp_path):
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/a.md", b"alpha")
    s3.put(f"{CORPUS_PREFIX}/b.md", b"beta")
    written = corpus.download_corpus(tmp_path)
    assert sorted(p.name for p in written) == ["a.md", "b.md"]
    assert (tmp_path / "a.md").read_bytes() == b"alpha"


def test_delete_from_corpus_removes_the_object():
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/gone.md", b"x")
    assert corpus.delete_from_corpus("gone.md") is True
    assert f"{CORPUS_PREFIX}/gone.md" not in s3.objects


def test_delete_reports_false_for_an_absent_object():
    corpus, _ = _corpus()
    assert corpus.delete_from_corpus("never-existed.md") is False


def test_corpus_filenames_are_flattened_to_a_basename():
    # A caller must not be able to write outside the corpus prefix by passing
    # a path. This is the only place a caller-supplied name becomes a key.
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__..%2F..%2Fetc%2Fpasswd", b"x")
    ref = UploadRef(key=f"{CHAT_PREFIX}/u1/abc__x", filename="../../evil.md", size=1)
    assert corpus.corpus_key(ref.filename) == f"{CORPUS_PREFIX}/evil.md"
```

- [ ] **Step 4: Run to verify they fail**

Run: `python -m pytest tests/test_s3.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb_mcp.s3'`.

- [ ] **Step 5: Implement**

Create `services/mcp/kb/kb_mcp/s3.py`:
```python
"""S3 access for the contributed corpus.

Every AWS call the service makes lives here, so the ingest pipeline stays a
pure filesystem walker and the tests need no network. The client is injected,
never constructed at import time.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

CHAT_PREFIX = "chat-uploads"
CORPUS_PREFIX = "knowledge/contributed"


class S3Error(RuntimeError):
    """An S3 operation failed. Never swallowed — a partial index is worse
    than a loud failure."""


@dataclass(frozen=True)
class UploadRef:
    key: str
    filename: str
    size: int


def _original_name(key: str) -> str:
    """LibreChat stores `<uuid>__<original name>`; recover the original."""
    base = key.rsplit("/", 1)[-1]
    return base.split("__", 1)[1] if "__" in base else base


class S3Corpus:
    def __init__(self, client, bucket: str):
        self.c, self.bucket = client, bucket

    def _list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.c.list_objects_v2(**kwargs)
            out.extend(resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return out
            token = resp.get("NextContinuationToken")

    def corpus_key(self, filename: str) -> str:
        """Caller-supplied names become keys only here, so this is the one
        place traversal has to be stopped. Flatten to a basename: a name like
        `../../evil.md` must not write outside the corpus prefix."""
        safe = posixpath.basename(filename.replace("\\", "/")).strip()
        if not safe or safe in (".", ".."):
            raise S3Error(f"unusable filename: {filename!r}")
        return f"{CORPUS_PREFIX}/{safe}"

    def find_user_upload(self, user_id: str, filename: str) -> UploadRef | None:
        """Newest matching upload under this user's own prefix, or None.

        Scoped to `chat-uploads/<user_id>/` so one contributor cannot reach
        another's file by guessing its name.
        """
        wanted = filename.strip().casefold()
        matches = []
        for obj in self._list(f"{CHAT_PREFIX}/{user_id}/"):
            name = _original_name(obj["Key"])
            if name.casefold() == wanted:
                matches.append((obj["LastModified"], obj["Key"], obj["Size"], name))
        if not matches:
            return None
        matches.sort(reverse=True)
        _, key, size, name = matches[0]
        return UploadRef(key=key, filename=name, size=size)

    def copy_to_corpus(self, ref: UploadRef) -> str:
        key = self.corpus_key(ref.filename)
        try:
            self.c.copy_object(
                Bucket=self.bucket, Key=key,
                CopySource={"Bucket": self.bucket, "Key": ref.key},
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise S3Error(f"could not copy {ref.key} into the corpus: {exc}") from exc
        return key

    def download_corpus(self, dest: Path) -> list[Path]:
        """Mirror the corpus prefix into `dest`. Returns the files written."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for obj in self._list(f"{CORPUS_PREFIX}/"):
            name = obj["Key"].rsplit("/", 1)[-1]
            if not name:
                continue
            try:
                body = self.c.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
            except Exception as exc:  # noqa: BLE001
                raise S3Error(f"could not read {obj['Key']}: {exc}") from exc
            path = dest / name
            path.write_bytes(body)
            written.append(path)
        return written

    def download_one(self, key: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise S3Error(f"could not read {key}: {exc}") from exc
        dest.write_bytes(body)
        return dest

    def delete_from_corpus(self, filename: str) -> bool:
        key = self.corpus_key(filename)
        existing = {o["Key"] for o in self._list(f"{CORPUS_PREFIX}/")}
        if key not in existing:
            return False
        self.c.delete_object(Bucket=self.bucket, Key=key)
        return True
```

- [ ] **Step 6: Run to verify they pass**

Run: `python -m pytest tests/test_s3.py -v`
Expected: PASS — 11 tests.

- [ ] **Step 7: Commit**

```bash
git add services/mcp/kb/kb_mcp/s3.py services/mcp/kb/tests/test_s3.py services/mcp/kb/tests/fakes.py services/mcp/kb/requirements.txt
git commit -m "feat(kb): S3 corpus access with per-user upload isolation"
```

---

### Task 8: Index a single document (reusable by both ingest paths)

`ingest()` walks the whole corpus. Upload needs to index exactly one file with extra provenance. Extract the shared body rather than writing a second pipeline.

**Files:**
- Modify: `services/mcp/kb/kb_mcp/ingest.py`
- Test: `services/mcp/kb/tests/test_ingest.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/mcp/kb/tests/test_ingest.py`:
```python
def test_ingest_file_indexes_one_document_with_provenance(tmp_path):
    from qdrant_client import QdrantClient

    from kb_mcp.ingest import ingest_file
    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"contributed": {}},
         "profiles": {"noc": {"scopes": ["contributed"], "description": "d"}}},
        source="test",
    )
    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()
    store = reg.for_scope("contributed")

    doc = tmp_path / "note.md"
    doc.write_text("Charger QNCH-1 reboots when the gun is unplugged mid-session.",
                   encoding="utf-8")

    n = ingest_file(
        doc, source="contributed/note.md", scope_name="contributed", store=store,
        embedder=emb, max_chars=1800, overlap=200, doc_hash="hash1",
        extra_payload={"uploaded_by": "noc@quench.local", "uploaded_at": "2026-08-23T10:00:00Z"},
    )
    assert n == 1

    hits = store.search(emb.embed(["gun unplugged mid-session"])[0], top_k=5)
    assert hits and hits[0]["source"] == "contributed/note.md"

    pts, _ = store.c.scroll(store.collection, limit=10, with_payload=True)
    payload = pts[0].payload
    assert payload["uploaded_by"] == "noc@quench.local"
    assert payload["uploaded_at"] == "2026-08-23T10:00:00Z"
    assert payload["scope"] == "contributed"


def test_ingest_file_returns_zero_for_unextractable_content(tmp_path):
    from qdrant_client import QdrantClient

    from kb_mcp.ingest import ingest_file
    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"contributed": {}},
         "profiles": {"noc": {"scopes": ["contributed"], "description": "d"}}},
        source="test",
    )
    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()

    doc = tmp_path / "blank.txt"
    doc.write_text("   \n\n  ", encoding="utf-8")
    n = ingest_file(doc, source="contributed/blank.txt", scope_name="contributed",
                    store=reg.for_scope("contributed"), embedder=emb,
                    max_chars=1800, overlap=200, doc_hash="h")
    assert n == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_ingest.py -v -k ingest_file`
Expected: FAIL with `ImportError: cannot import name 'ingest_file'`.

- [ ] **Step 3: Implement**

In `services/mcp/kb/kb_mcp/ingest.py`, add above `ingest()`:
```python
from .parse import parse_file, SUPPORTED, TABULAR  # TABULAR added in step 5


def ingest_file(path, source: str, scope_name: str, store, embedder, *,
                max_chars: int, overlap: int, doc_hash: str,
                extra_payload: dict | None = None) -> int:
    """Index exactly one document, replacing any previous version of it.

    Shared by the corpus walk and by chat upload, so both paths chunk, embed
    and stamp payloads identically. Returns the chunk count; 0 means nothing
    was extractable (a scanned PDF or an empty file), which callers must treat
    as a failure rather than a silent success.
    """
    from pathlib import Path

    path = Path(path)
    store.delete_source(source)  # replace old version (no-op if new)
    splitter = chunk_tabular if path.suffix.lower() in TABULAR else chunk_text

    chunks: list[dict] = []
    for seg in parse_file(path):
        for text in splitter(seg["text"], max_chars, overlap):
            chunk = {
                "source": source, "scope": scope_name, "location": seg["location"],
                "text": text, "doc_hash": doc_hash, "chunk_idx": len(chunks),
            }
            if extra_payload:
                chunk.update(extra_payload)
            chunks.append(chunk)

    if not chunks:
        return 0
    store.upsert(chunks, embedder.embed([c["text"] for c in chunks]))
    return len(chunks)
```

Update the import at the top of `ingest.py`:
```python
from .chunk import chunk_tabular, chunk_text
```

- [ ] **Step 4: Declare which extensions are tabular**

In `services/mcp/kb/kb_mcp/parse.py`, add below `SUPPORTED`:
```python
# Row-oriented formats whose chunks must carry the header line (see chunk_tabular).
TABULAR = {".xlsx", ".csv"}
```

- [ ] **Step 5: Rewrite `ingest()` to use the shared helper**

In `ingest.py`, replace the body of the per-file loop inside `ingest()` — from `store.delete_source(source)` through the `stats["chunks"] += len(chunks)` line — with:
```python
        n = ingest_file(path, source=source, scope_name=scope.name, store=store,
                        embedder=embedder, max_chars=max_chars, overlap=overlap,
                        doc_hash=h)
        if n == 0:
            stats["empty_docs"] += 1
            log.warning("%s produced no text — scanned PDF or empty file?", source)
            continue
        stats["indexed_docs"] += 1
        stats["chunks"] += n
```

- [ ] **Step 6: Make one unreadable file not abort a whole batch**

Found during Task 3's code review: a corrupt or mis-named file raises out of the parser
(`PackageNotFoundError` for a fake `.docx`, and the `.pdf` branch has the same exposure).
`parse_file` is called with no guard, so a single bad file aborts the entire ingest run —
and on the upload path it would surface a raw library exception to a NOC engineer.

Guard it once, centrally, where both paths meet. Add to `services/mcp/kb/kb_mcp/parse.py`:
```python
class DocumentUnreadable(ValueError):
    """The file could not be opened as the type its extension claims."""
```

Wrap the parse call inside `ingest_file`:
```python
    try:
        segments = parse_file(path)
    except Exception as exc:  # noqa: BLE001 - any parser failure is the same to callers
        raise DocumentUnreadable(
            f"{path.name} could not be read as {path.suffix} — the file may be corrupt, "
            "or saved in a different format than its extension claims."
        ) from exc

    for seg in segments:
```
(replacing the bare `for seg in parse_file(path):` line, and importing `DocumentUnreadable`
alongside the other `parse` imports).

Then make the batch walk survive it — in `ingest()`, wrap the `ingest_file` call:
```python
        try:
            n = ingest_file(path, source=source, scope_name=scope.name, store=store,
                            embedder=embedder, max_chars=max_chars, overlap=overlap,
                            doc_hash=h)
        except DocumentUnreadable as exc:
            stats["unreadable_docs"] += 1
            log.warning("%s", exc)
            continue
```
and add `"unreadable_docs": 0` to the `stats` dict initialiser.

Test it — append to `tests/test_ingest.py`:
```python
def test_unreadable_file_raises_rather_than_indexing_nothing(tmp_path):
    from qdrant_client import QdrantClient

    from kb_mcp.ingest import ingest_file
    from kb_mcp.parse import DocumentUnreadable
    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"contributed": {}},
         "profiles": {"noc": {"scopes": ["contributed"], "description": "d"}}},
        source="test",
    )
    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()

    fake = tmp_path / "not-really.docx"
    fake.write_text("this is plain text pretending to be Word", encoding="utf-8")

    with pytest.raises(DocumentUnreadable, match="may be corrupt"):
        ingest_file(fake, source="contributed/not-really.docx", scope_name="contributed",
                    store=reg.for_scope("contributed"), embedder=emb,
                    max_chars=1800, overlap=200, doc_hash="h")


def test_batch_ingest_skips_an_unreadable_file_and_keeps_going(tmp_path):
    """One bad document must not cost the whole corpus its index."""
    from qdrant_client import QdrantClient

    from kb_mcp.ingest import ingest
    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"ev-core": {"folder": "ev-core"}},
         "profiles": {"noc": {"scopes": ["ev-core"], "description": "d"}}},
        source="test",
    )
    scope_dir = tmp_path / "ev-core"
    scope_dir.mkdir()
    (scope_dir / "good.md").write_text("Connector lock failure recovery steps.", encoding="utf-8")
    (scope_dir / "bad.docx").write_text("not a real docx", encoding="utf-8")

    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()

    stats = ingest(tmp_path, cfg, reg, emb, max_chars=1800, overlap=200)
    assert stats["indexed_docs"] == 1
    assert stats["unreadable_docs"] == 1
```

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS — the pre-existing ingest tests must still pass, proving the refactor changed no behaviour.

- [ ] **Step 8: Commit**

```bash
git add services/mcp/kb/kb_mcp/ingest.py services/mcp/kb/kb_mcp/parse.py services/mcp/kb/tests/test_ingest.py
git commit -m "refactor(kb): extract ingest_file and survive one unreadable document"
```

---

### Task 9: List indexed documents

**Files:**
- Modify: `services/mcp/kb/kb_mcp/store.py`
- Test: `services/mcp/kb/tests/test_store.py`

- [ ] **Step 1: Write the failing test**

Append to `services/mcp/kb/tests/test_store.py`:
```python
def test_list_documents_groups_chunks_by_source_with_provenance():
    from qdrant_client import QdrantClient

    from kb_mcp.scopes import parse_scopes
    from kb_mcp.store import StoreRegistry
    from tests.fakes import FakeEmbedder

    cfg = parse_scopes(
        {"scopes": {"ev-core": {}, "contributed": {}},
         "profiles": {"noc": {"scopes": ["ev-core", "contributed"], "description": "d"}}},
        source="test",
    )
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), cfg, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    store = reg.for_scope("contributed")

    rows = [
        {"source": "contributed/a.md", "scope": "contributed", "location": "a.md",
         "text": "alpha one", "doc_hash": "h1", "chunk_idx": 0,
         "uploaded_by": "noc@quench.local", "uploaded_at": "2026-08-23T10:00:00Z"},
        {"source": "contributed/a.md", "scope": "contributed", "location": "a.md",
         "text": "alpha two", "doc_hash": "h1", "chunk_idx": 1,
         "uploaded_by": "noc@quench.local", "uploaded_at": "2026-08-23T10:00:00Z"},
        {"source": "ev-core/ocpp.md", "scope": "ev-core", "location": "ocpp.md",
         "text": "curated", "doc_hash": "h2", "chunk_idx": 0},
    ]
    store.upsert(rows, e.embed([r["text"] for r in rows]))

    everything = store.list_documents()
    assert [d["source"] for d in everything] == ["contributed/a.md", "ev-core/ocpp.md"]
    a = everything[0]
    assert a["chunks"] == 2
    assert a["uploaded_by"] == "noc@quench.local"
    assert a["scope"] == "contributed"

    only_contributed = store.list_documents(scopes=["contributed"])
    assert [d["source"] for d in only_contributed] == ["contributed/a.md"]

    assert store.list_documents(scopes=[]) == []


def test_list_documents_on_a_missing_collection_is_empty():
    from qdrant_client import QdrantClient

    from kb_mcp.store import KbStore

    store = KbStore(QdrantClient(":memory:"), "never_created", dim=8)
    assert store.list_documents() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_store.py -v -k list_documents`
Expected: FAIL with `AttributeError: 'KbStore' object has no attribute 'list_documents'`.

- [ ] **Step 3: Implement**

In `services/mcp/kb/kb_mcp/store.py`, add to `KbStore` after `known_sources`:
```python
    def list_documents(self, scopes: list[str] | None = None) -> list[dict]:
        """One row per source document: chunk count plus the provenance
        stamped at ingest. `known_sources` answers "what keys exist" for
        pruning; this answers "what is in the KB" for a human."""
        if not self.c.collection_exists(self.collection):
            return []
        flt = None
        if scopes is not None:
            flt = Filter(must=[FieldCondition(key="scope", match=MatchAny(any=list(scopes)))])

        docs: dict[str, dict] = {}
        offset = None
        while True:
            pts, offset = self.c.scroll(
                self.collection, scroll_filter=flt, limit=256, offset=offset, with_payload=True
            )
            for p in pts:
                pl = p.payload
                row = docs.setdefault(pl["source"], {
                    "source": pl["source"], "scope": pl.get("scope"), "chunks": 0,
                    "uploaded_by": pl.get("uploaded_by"), "uploaded_at": pl.get("uploaded_at"),
                })
                row["chunks"] += 1
            if offset is None:
                break
        return sorted(docs.values(), key=lambda r: r["source"])
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/mcp/kb/kb_mcp/store.py services/mcp/kb/tests/test_store.py
git commit -m "feat(kb): list indexed documents with chunk counts and provenance"
```

---

### Task 10: Configuration settings

**Files:**
- Modify: `services/mcp/kb/kb_mcp/config.py`
- Test: `services/mcp/kb/tests/test_config.py` (new)

- [ ] **Step 1: Write the failing test**

Create `services/mcp/kb/tests/test_config.py`:
```python
from kb_mcp.config import Settings


def test_contributor_roles_parse_into_a_tuple():
    s = Settings(kb_contributor_roles="kb-contributor,ADMIN")
    assert s.contributor_roles == ("kb-contributor", "ADMIN")


def test_contributor_roles_tolerate_spaces_and_empties():
    s = Settings(kb_contributor_roles=" kb-contributor , , ADMIN ")
    assert s.contributor_roles == ("kb-contributor", "ADMIN")


def test_empty_contributor_roles_grants_nobody():
    assert Settings(kb_contributor_roles="").contributor_roles == ()


def test_upload_defaults():
    s = Settings()
    assert s.kb_max_upload_mb == 25
    assert s.contributed_scope == "contributed"
    assert s.contributed_dir == "/knowledge/contributed"
    assert s.aws_region == "ap-south-1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'contributor_roles'`.

- [ ] **Step 3: Implement**

In `services/mcp/kb/kb_mcp/config.py`, add fields to `Settings` before `mcp_transport`:
```python
    # --- contributed uploads (see 2026-08-23-kb-document-upload-design.md) ---
    s3_bucket: str = ""                       # empty disables the write tools entirely
    aws_region: str = "ap-south-1"
    contributed_scope: str = "contributed"
    contributed_dir: str = "/knowledge/contributed"  # writable volume nested in the ro mount
    kb_contributor_roles: str = "kb-contributor,ADMIN"
    kb_max_upload_mb: int = 25
```

And add a property after the field block:
```python
    @property
    def contributor_roles(self) -> tuple[str, ...]:
        """Comma-separated env value -> tuple. Blank entries are dropped so a
        stray comma cannot create a role that matches a caller with none."""
        return tuple(r.strip() for r in self.kb_contributor_roles.split(",") if r.strip())
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/mcp/kb/kb_mcp/config.py services/mcp/kb/tests/test_config.py
git commit -m "feat(kb): settings for contributed uploads and contributor roles"
```

---

### Task 11: The writer — upload, remove, list

**Files:**
- Create: `services/mcp/kb/kb_mcp/writes.py`
- Test: `services/mcp/kb/tests/test_writes.py`

- [ ] **Step 1: Write the failing tests**

Create `services/mcp/kb/tests/test_writes.py`:
```python
import pytest
from qdrant_client import QdrantClient

from kb_mcp.identity import Caller
from kb_mcp.s3 import CHAT_PREFIX, CORPUS_PREFIX, S3Corpus
from kb_mcp.scopes import parse_scopes
from kb_mcp.store import StoreRegistry
from kb_mcp.writes import KbWriter, PermissionDenied, UploadRejected
from tests.fakes import FakeEmbedder, FakeS3

CFG = parse_scopes(
    {"scopes": {"ev-core": {}, "contributed": {}},
     "profiles": {"noc": {"scopes": ["ev-core", "contributed"], "description": "d"}}},
    source="test",
)
CONTRIBUTOR = Caller("u1", "noc@quench.local", "kb-contributor")
OUTSIDER = Caller("u2", "guest@quench.local", "noc-engineer")


def _writer(tmp_path, max_mb=25):
    s3 = FakeS3()
    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()
    writer = KbWriter(
        corpus=S3Corpus(s3, "test-bucket"), registry=reg, embedder=emb,
        scope="contributed", cache_dir=tmp_path, contributor_roles=("kb-contributor", "ADMIN"),
        max_upload_mb=max_mb, max_chars=1800, overlap=200,
        now=lambda: "2026-08-23T10:00:00Z",
    )
    return writer, s3, reg, emb


def test_upload_indexes_the_document_and_returns_a_summary(tmp_path):
    writer, s3, reg, emb = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md",
           b"Charger QNCH-1 reboots when the gun is unplugged mid-session.")

    result = writer.upload(CONTRIBUTOR, "note.md")

    assert result["source"] == "contributed/note.md"
    assert result["chunks"] == 1
    assert result["scope"] == "contributed"
    assert s3.objects[f"{CORPUS_PREFIX}/note.md"]

    hits = reg.for_scope("contributed").search(emb.embed(["gun unplugged"])[0], top_k=3)
    assert hits[0]["source"] == "contributed/note.md"


def test_upload_stamps_uploader_and_timestamp(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"body text here")
    writer.upload(CONTRIBUTOR, "note.md")

    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert docs[0]["uploaded_by"] == "noc@quench.local"
    assert docs[0]["uploaded_at"] == "2026-08-23T10:00:00Z"


def test_non_contributor_is_refused(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__note.md", b"body")
    with pytest.raises(PermissionDenied, match="kb-contributor"):
        writer.upload(OUTSIDER, "note.md")


def test_refusal_happens_before_any_write(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__note.md", b"body")
    with pytest.raises(PermissionDenied):
        writer.upload(OUTSIDER, "note.md")
    assert f"{CORPUS_PREFIX}/note.md" not in s3.objects


def test_another_users_file_is_not_reachable(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__private.md", b"someone else's")
    with pytest.raises(UploadRejected, match="No upload named"):
        writer.upload(CONTRIBUTOR, "private.md")


def test_absent_and_forbidden_files_give_the_same_message(tmp_path):
    """The tool must not become an existence oracle for other users' files."""
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__theirs.md", b"x")
    with pytest.raises(UploadRejected) as forbidden:
        writer.upload(CONTRIBUTOR, "theirs.md")
    with pytest.raises(UploadRejected) as absent:
        writer.upload(CONTRIBUTOR, "nothing-like-this.md")
    assert str(forbidden.value) == str(absent.value)


def test_unsupported_extension_is_refused_with_the_accepted_list(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__slides.pptx", b"x")
    with pytest.raises(UploadRejected, match=r"\.docx"):
        writer.upload(CONTRIBUTOR, "slides.pptx")


def test_legacy_doc_gets_a_specific_message(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__old.doc", b"x")
    with pytest.raises(UploadRejected, match="re-save it as .docx"):
        writer.upload(CONTRIBUTOR, "old.doc")


def test_oversize_upload_is_refused_before_download(tmp_path):
    writer, s3, _, _ = _writer(tmp_path, max_mb=1)
    s3.put(f"{CHAT_PREFIX}/u1/abc__big.md", b"x" * (2 * 1024 * 1024))
    with pytest.raises(UploadRejected, match="1 MB"):
        writer.upload(CONTRIBUTOR, "big.md")
    assert f"{CORPUS_PREFIX}/big.md" not in s3.objects


def test_unextractable_document_fails_loudly(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__blank.txt", b"   \n\n   ")
    with pytest.raises(UploadRejected, match="no readable text"):
        writer.upload(CONTRIBUTOR, "blank.txt")


def test_identical_bytes_are_reported_as_already_indexed(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"stable content for hashing")
    first = writer.upload(CONTRIBUTOR, "note.md")
    second = writer.upload(CONTRIBUTOR, "note.md")

    assert first["chunks"] == 1
    assert second["already_indexed"] is True
    assert second["chunks"] == 1
    assert len(reg.for_scope("contributed").list_documents(scopes=["contributed"])) == 1


def test_changed_bytes_replace_the_previous_version(tmp_path):
    writer, s3, reg, emb = _writer(tmp_path)
    key = f"{CHAT_PREFIX}/u1/abc__note.md"
    s3.put(key, b"first version, about connector wear")
    writer.upload(CONTRIBUTOR, "note.md")

    s3.put(key, b"second version, about firmware rollback procedure")
    result = writer.upload(CONTRIBUTOR, "note.md")

    assert result["already_indexed"] is False
    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert len(docs) == 1, "a replacement must not leave the old version behind"

    hits = reg.for_scope("contributed").search(emb.embed(["firmware rollback"])[0], top_k=3)
    assert hits and "firmware rollback" in hits[0]["text"]


def test_remove_deletes_the_object_and_its_chunks(tmp_path):
    writer, s3, reg, emb = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content to be removed later")
    writer.upload(CONTRIBUTOR, "note.md")

    result = writer.remove(CONTRIBUTOR, "contributed/note.md")

    assert result["removed"] is True
    assert f"{CORPUS_PREFIX}/note.md" not in s3.objects
    assert reg.for_scope("contributed").list_documents(scopes=["contributed"]) == []


def test_remove_is_refused_for_non_contributors(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content")
    writer.upload(CONTRIBUTOR, "note.md")
    with pytest.raises(PermissionDenied):
        writer.remove(OUTSIDER, "contributed/note.md")


def test_remove_refuses_to_touch_the_curated_scope(tmp_path):
    writer, _, _, _ = _writer(tmp_path)
    with pytest.raises(UploadRejected, match="contributed"):
        writer.remove(CONTRIBUTOR, "ev-core/ocpp.md")


def test_remove_reports_a_missing_document(tmp_path):
    writer, _, _, _ = _writer(tmp_path)
    result = writer.remove(CONTRIBUTOR, "contributed/never-existed.md")
    assert result["removed"] is False


def test_list_documents_needs_no_special_role(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"listed content")
    writer.upload(CONTRIBUTOR, "note.md")
    docs = writer.list_documents(OUTSIDER)
    assert [d["source"] for d in docs] == ["contributed/note.md"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_writes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'kb_mcp.writes'`.

- [ ] **Step 3: Implement**

Create `services/mcp/kb/kb_mcp/writes.py`:
```python
"""Write operations on the contributed corpus.

Separated from server.py so the rules — who may write, what is accepted, what
happens on failure — are testable without FastMCP, and so server.py stays
thin wiring.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from .identity import Caller, is_contributor
from .ingest import ingest_file
from .parse import SUPPORTED, DocumentUnreadable
from .s3 import S3Corpus

log = logging.getLogger(__name__)

ACCEPTED = ", ".join(sorted(SUPPORTED))


class PermissionDenied(PermissionError):
    """Caller lacks a contributor role."""


class UploadRejected(ValueError):
    """The upload cannot be indexed, with a reason the user can act on."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KbWriter:
    def __init__(self, *, corpus: S3Corpus, registry, embedder, scope: str,
                 cache_dir, contributor_roles: tuple[str, ...], max_upload_mb: int,
                 max_chars: int, overlap: int, now=_utc_now):
        self.corpus = corpus
        self.registry = registry
        self.embedder = embedder
        self.scope = scope
        self.cache_dir = Path(cache_dir)
        self.roles = contributor_roles
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.max_upload_mb = max_upload_mb
        self.max_chars = max_chars
        self.overlap = overlap
        self.now = now

    # -- guards ---------------------------------------------------------

    def _require_contributor(self, caller: Caller, action: str) -> None:
        if not is_contributor(caller, self.roles):
            log.warning("denied %s for user=%s role=%r", action, caller.user_id, caller.role)
            raise PermissionDenied(
                f"You do not have permission to {action} the knowledge base. This "
                f"requires one of these roles: {', '.join(self.roles) or '(none configured)'}. "
                "Ask an administrator to grant it in the LibreChat admin panel."
            )

    def _check_extension(self, filename: str) -> None:
        ext = Path(filename).suffix.lower()
        if ext == ".doc":
            raise UploadRejected(
                "Legacy .doc files cannot be read. Open it in Word and re-save it as .docx, "
                "then upload again."
            )
        if ext not in SUPPORTED:
            raise UploadRejected(
                f"{filename!r} is not a supported document type. Accepted: {ACCEPTED}."
            )

    # -- operations -----------------------------------------------------

    def upload(self, caller: Caller, filename: str) -> dict:
        self._require_contributor(caller, "add documents to")
        self._check_extension(filename)

        ref = self.corpus.find_user_upload(caller.user_id, filename)
        if ref is None:
            # Deliberately identical whether the file is absent or belongs to
            # someone else: this must not become an existence oracle.
            raise UploadRejected(
                f"No upload named {filename!r} was found. Attach the file to this chat "
                "first, then ask again."
            )
        if ref.size > self.max_bytes:
            raise UploadRejected(
                f"{filename!r} is {ref.size / 1024 / 1024:.1f} MB, over the "
                f"{self.max_upload_mb} MB limit."
            )

        local = self.corpus.download_one(ref.key, self.cache_dir / ref.filename)
        doc_hash = hashlib.sha256(local.read_bytes()).hexdigest()
        source = f"{self.scope}/{ref.filename}"
        store = self.registry.for_scope(self.scope)

        if store.current_doc_hash(source) == doc_hash:
            existing = [d for d in store.list_documents(scopes=[self.scope])
                        if d["source"] == source]
            return {"source": source, "scope": self.scope, "already_indexed": True,
                    "chunks": existing[0]["chunks"] if existing else 0,
                    "message": f"{ref.filename} is already in the knowledge base, unchanged."}

        self.corpus.copy_to_corpus(ref)
        try:
            chunks = ingest_file(
                local, source=source, scope_name=self.scope, store=store,
                embedder=self.embedder, max_chars=self.max_chars, overlap=self.overlap,
                doc_hash=doc_hash,
                extra_payload={"uploaded_by": caller.email or caller.user_id,
                               "uploaded_at": self.now()},
            )
        except DocumentUnreadable as exc:
            # The object was already copied; take it back out so a failed upload
            # leaves nothing behind for a later corpus walk to trip over.
            self.corpus.delete_from_corpus(ref.filename)
            raise UploadRejected(str(exc)) from exc
        if chunks == 0:
            # Leave nothing half-done: the object was copied, so take it back out.
            self.corpus.delete_from_corpus(ref.filename)
            raise UploadRejected(
                f"{ref.filename!r} produced no readable text — it may be a scanned PDF or "
                "an empty file. Scanned documents need OCR before they can be indexed."
            )

        log.info("indexed %s (%d chunks) for user=%s", source, chunks, caller.user_id)
        return {"source": source, "scope": self.scope, "chunks": chunks,
                "already_indexed": False,
                "message": f"{ref.filename} is now searchable ({chunks} chunks)."}

    def remove(self, caller: Caller, source: str) -> dict:
        self._require_contributor(caller, "remove documents from")
        prefix = f"{self.scope}/"
        if not source.startswith(prefix):
            raise UploadRejected(
                f"Only documents in the '{self.scope}' scope can be removed here; "
                f"{source!r} is curated material. Curated documents are managed in git."
            )
        filename = source[len(prefix):]
        store = self.registry.for_scope(self.scope)
        indexed = any(d["source"] == source for d in store.list_documents(scopes=[self.scope]))
        deleted = self.corpus.delete_from_corpus(filename)
        if indexed:
            store.delete_source(source)
        log.info("removed %s (object=%s, indexed=%s) for user=%s",
                 source, deleted, indexed, caller.user_id)
        return {"source": source, "removed": bool(indexed or deleted)}

    def list_documents(self, caller: Caller) -> list[dict]:
        """Read-only: any authenticated caller may see what is loaded."""
        return self.registry.for_scope(self.scope).list_documents(scopes=[self.scope])
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_writes.py -v`
Expected: PASS — 17 tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add services/mcp/kb/kb_mcp/writes.py services/mcp/kb/tests/test_writes.py
git commit -m "feat(kb): role-gated upload, remove and list for the contributed corpus"
```

---

### Task 12: Register the write tools on the MCP server

**Files:**
- Modify: `services/mcp/kb/kb_mcp/server.py`
- Test: `services/mcp/kb/tests/test_server.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/mcp/kb/tests/test_server.py`:
```python
def _writer_server(tmp_path, headers):
    """Build a server with write tools and a stubbed header source."""
    from kb_mcp.s3 import S3Corpus
    from kb_mcp.server import build_server
    from kb_mcp.writes import KbWriter
    from tests.fakes import FakeS3

    s3 = FakeS3()
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    writer = KbWriter(
        corpus=S3Corpus(s3, "b"), registry=reg, embedder=e, scope="ev-core",
        cache_dir=tmp_path, contributor_roles=("kb-contributor",), max_upload_mb=25,
        max_chars=1800, overlap=200, now=lambda: "2026-08-23T10:00:00Z",
    )
    mcp = build_server(config=CFG, registry=reg, embedder=e, writer=writer,
                       headers_source=lambda: headers)
    return mcp, s3


@pytest.mark.asyncio
async def test_write_tools_are_registered_when_a_writer_is_supplied(tmp_path):
    mcp, _ = _writer_server(tmp_path, {})
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert {"kb_upload_document", "kb_remove_document", "kb_list_documents"} <= names


@pytest.mark.asyncio
async def test_write_tools_are_absent_without_a_writer():
    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    reg.ensure_all()
    mcp = build_server(config=CFG, registry=reg, embedder=e)
    async with Client(mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert "kb_upload_document" not in names
    assert "kb_search_noc" in names


@pytest.mark.asyncio
async def test_upload_tool_succeeds_for_a_contributor(tmp_path):
    headers = {"x-user-id": "u1", "x-user-email": "noc@quench.local",
               "x-user-role": "kb-contributor"}
    mcp, s3 = _writer_server(tmp_path, headers)
    s3.put("chat-uploads/u1/abc__note.md", b"Gun 2 fails to unlock after a session ends.")
    async with Client(mcp) as client:
        result = await client.call_tool("kb_upload_document", {"filename": "note.md"})
    payload = json.loads(result.content[0].text)
    assert payload["chunks"] == 1
    assert payload["source"] == "ev-core/note.md"


@pytest.mark.asyncio
async def test_upload_tool_refuses_a_non_contributor(tmp_path):
    headers = {"x-user-id": "u1", "x-user-email": "a@b.com", "x-user-role": "noc-engineer"}
    mcp, s3 = _writer_server(tmp_path, headers)
    s3.put("chat-uploads/u1/abc__note.md", b"body")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="permission"):
            await client.call_tool("kb_upload_document", {"filename": "note.md"})


@pytest.mark.asyncio
async def test_upload_tool_refuses_when_identity_headers_are_missing(tmp_path):
    mcp, s3 = _writer_server(tmp_path, {})
    s3.put("chat-uploads/u1/abc__note.md", b"body")
    async with Client(mcp) as client:
        with pytest.raises(ToolError, match="X-User-Id"):
            await client.call_tool("kb_upload_document", {"filename": "note.md"})
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_server.py -v -k "write_tools or upload_tool"`
Expected: FAIL — `build_server()` takes no `writer` argument.

- [ ] **Step 3: Implement**

In `services/mcp/kb/kb_mcp/server.py`, extend the signature and add registration. Replace the `build_server` definition line and add the writer wiring before `return mcp`:

```python
def build_server(config: ScopesConfig | None = None, registry=None, embedder=None,
                 writer=None, headers_source=None) -> FastMCP:
```

Add near the top of the file:
```python
from .identity import parse_caller
```

Then, immediately before `return mcp` in `build_server`:
```python
    if writer is not None:
        _register_writes(mcp, writer, headers_source)

    return mcp


def _caller_from(headers_source):
    """Resolve the caller for the current request.

    `get_http_headers()` returns {} when there is no HTTP request at all, and
    parse_caller turns that into a refusal — a missing identity is never an
    implicit grant.
    """
    if headers_source is None:  # pragma: no cover - real wiring
        from fastmcp.server.dependencies import get_http_headers

        headers_source = get_http_headers
    return parse_caller(headers_source())


def _register_writes(mcp: FastMCP, writer, headers_source) -> None:
    accepted = ", ".join(sorted(SUPPORTED))

    @mcp.tool(
        name="kb_upload_document",
        description=(
            "Add a document the user has ALREADY attached to this chat into the knowledge "
            "base, making it searchable by everyone who can search this KB. Pass the "
            f"file's name exactly as attached. Accepted types: {accepted}. Requires a "
            "contributor role; the caller's identity comes from the request, not from you, "
            "so do not ask the user for it and do not claim a role on their behalf. "
            "Spreadsheets index well when they are textual (issue lists, test matrices, "
            "notes); for live numeric fleet or charger data prefer the NOC and CMS tools, "
            "which query current systems instead of a stale export."
        ),
    )
    async def kb_upload_document(filename: str) -> dict:
        return writer.upload(_caller_from(headers_source), filename)

    @mcp.tool(
        name="kb_remove_document",
        description=(
            "Remove a previously uploaded document from the knowledge base by its `source` "
            "value (as shown by kb_list_documents), deleting both the stored file and its "
            "indexed text. Only uploaded documents can be removed; curated material is "
            "managed in git. Requires a contributor role."
        ),
    )
    async def kb_remove_document(source: str) -> dict:
        return writer.remove(_caller_from(headers_source), source)

    @mcp.tool(
        name="kb_list_documents",
        description=(
            "List the documents users have uploaded into the knowledge base, with who "
            "added each one, when, and how many chunks it produced. Read-only; any user "
            "may call it. Use it to check whether something is already loaded before "
            "uploading, and to find the `source` value kb_remove_document needs."
        ),
    )
    async def kb_list_documents() -> list[dict]:
        return writer.list_documents(_caller_from(headers_source))
```

Add to the imports at the top of `server.py` (`parse.py` has no heavy module-scope
imports — PyMuPDF, python-docx and openpyxl are all imported lazily inside their
parsers — so this does not slow server start or drag a model download into tests):
```python
from .parse import SUPPORTED
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS — new and pre-existing server tests.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/mcp/kb/kb_mcp/server.py services/mcp/kb/tests/test_server.py
git commit -m "feat(kb): register kb_upload_document, kb_remove_document and kb_list_documents"
```

---

### Task 13: Wire the real dependencies at startup

**Files:**
- Modify: `services/mcp/kb/kb_mcp/server.py`
- Modify: `services/mcp/kb/kb_mcp/__main__.py`
- Test: `services/mcp/kb/tests/test_server.py`

- [ ] **Step 1: Write the failing test**

Append to `services/mcp/kb/tests/test_server.py`:
```python
def test_build_writer_returns_none_without_a_bucket(monkeypatch):
    from kb_mcp.config import Settings
    from kb_mcp.server import build_writer

    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    settings = Settings(s3_bucket="")
    assert build_writer(settings, CFG, reg, e) is None


def test_build_writer_returns_none_when_the_scope_is_undeclared():
    from kb_mcp.config import Settings
    from kb_mcp.server import build_writer

    e = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=e.dim)
    settings = Settings(s3_bucket="b", contributed_scope="not-declared")
    assert build_writer(settings, CFG, reg, e) is None
```

`CFG` in `test_server.py` declares `ev-core`, `chargezone` and `hr` — not `contributed` — so the second test exercises a real misconfiguration.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_server.py -v -k build_writer`
Expected: FAIL with `ImportError: cannot import name 'build_writer'`.

- [ ] **Step 3: Implement**

Append to `services/mcp/kb/kb_mcp/server.py`:
```python
def build_writer(settings, config: ScopesConfig, registry, embedder):
    """Assemble the KbWriter, or return None if uploads are not configured.

    Returning None rather than raising is deliberate: a KB server with no
    bucket must still serve search. The write tools simply do not appear, which
    is a clearer signal than tools that exist and always fail.
    """
    if not settings.s3_bucket:
        log.warning("S3_BUCKET is unset — upload tools disabled, search still available")
        return None
    if settings.contributed_scope not in config.scopes:
        log.warning(
            "contributed scope %r is not declared in scopes.yaml — upload tools disabled",
            settings.contributed_scope,
        )
        return None

    import boto3  # lazy: tests must never need AWS libraries at import time

    from .s3 import S3Corpus
    from .writes import KbWriter

    client = boto3.client("s3", region_name=settings.aws_region)
    return KbWriter(
        corpus=S3Corpus(client, settings.s3_bucket),
        registry=registry, embedder=embedder,
        scope=settings.contributed_scope,
        cache_dir=settings.contributed_dir,
        contributor_roles=settings.contributor_roles,
        max_upload_mb=settings.kb_max_upload_mb,
        max_chars=settings.chunk_max_chars,
        overlap=settings.chunk_overlap_chars,
    )
```

Add at the top of `server.py`:
```python
import logging

log = logging.getLogger(__name__)
```

Then, inside `build_server`, replace the `if writer is not None:` guard with real wiring that also builds one when none was injected:
```python
    if writer is None:  # pragma: no cover - real wiring
        writer = build_writer(settings, config, registry, embedder)
    if writer is not None:
        _register_writes(mcp, writer, headers_source)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python -m pytest tests/test_server.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite**

Run: `python -m pytest -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add services/mcp/kb/kb_mcp/server.py services/mcp/kb/tests/test_server.py
git commit -m "feat(kb): wire the S3 writer at startup, degrading to search-only when unset"
```

---

### Task 14: Declare the `contributed` scope

**Files:**
- Modify: `knowledge/scopes.yaml`
- Modify: `knowledge/MANIFEST.md`

- [ ] **Step 1: Add the scope**

In `knowledge/scopes.yaml`, replace the `scopes:` block:
```yaml
scopes:
  ev-core:
    folder: ev-core
  # User-contributed uploads (kb_upload_document). Kept apart from ev-core so
  # curated OCPP material stays separable, prunable and attributable, and a bad
  # batch can be removed without touching the vetted corpus. Backed by a
  # writable volume at /knowledge/contributed and by S3.
  contributed:
    folder: contributed
```

And extend the profile:
```yaml
profiles:
  noc:
    scopes: [ev-core, contributed]
    description: >-
      EV/OCPP protocol reference, charger model specs and firmware notes, NOC
      operational runbooks, and documents contributed by the NOC team.
```

- [ ] **Step 2: Note the split in the manifest**

Append to `knowledge/MANIFEST.md`:
```markdown
## Contributed scope

Documents uploaded from chat with `kb_upload_document` land in the `contributed`
scope and are **not** tracked here. This file cannot record them: the KB container
has no write access to the repository, and a manifest updated by a running service
would drift from git on every commit.

`kb_list_documents` is the live source of truth for that scope — it reports each
document with its uploader, date and chunk count. The files themselves live in
`s3://quench-manthan-kb/knowledge/contributed/`.
```

- [ ] **Step 3: Verify the config still parses**

Run:
```bash
cd services/mcp/kb && python -c "
from kb_mcp.scopes import load_scopes
cfg = load_scopes('../../../knowledge/scopes.yaml')
print('scopes:', sorted(cfg.scopes))
print('noc profile:', cfg.profiles['noc'].scopes)
"
```
Expected:
```
scopes: ['contributed', 'ev-core']
noc profile: ('ev-core', 'contributed')
```

- [ ] **Step 4: Commit**

```bash
git add knowledge/scopes.yaml knowledge/MANIFEST.md
git commit -m "feat(kb): declare the contributed scope and bundle it into the noc profile"
```

---

### Task 15: Container wiring — volumes, environment, dependencies

**Files:**
- Modify: `apps/ai-platform/librechat/docker-compose.yml:108-124`
- Modify: `apps/ai-platform/librechat/.env` (gitignored — not committed)

- [ ] **Step 1: Update the kb service definition**

In `apps/ai-platform/librechat/docker-compose.yml`, replace the `kb-mcp-server` block:
```yaml
  # Read-only RAG knowledge base over EV/SME docs (Qdrant + local BGE embedder),
  # plus role-gated chat upload into the `contributed` scope (S3-backed).
  kb-mcp-server:
    build: ../../../services/mcp/kb
    container_name: kb-mcp-server
    depends_on:
      - qdrant
    environment:
      - MCP_TRANSPORT=http
      - MCP_HOST=0.0.0.0
      - MCP_PORT=8106
      - QDRANT_URL=http://qdrant:6333
      - KNOWLEDGE_DIR=/knowledge
      - S3_BUCKET=quench-manthan-kb
      - AWS_REGION=ap-south-1
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
      - KB_CONTRIBUTOR_ROLES=kb-contributor,ADMIN
      - KB_MAX_UPLOAD_MB=25
    volumes:
      - ../../../knowledge:/knowledge:ro
      # Writable volume nested inside the read-only mount: the corpus walker
      # already treats a top-level folder as a scope, so `contributed` needs no
      # code change — only somewhere writable to land.
      - kb-contributed:/knowledge/contributed
      # The fastembed model is ~209 MB and lived in the container's writable
      # layer, so every rebuild re-downloaded it before the first ingest could
      # run. A volume makes rebuilds cheap.
      - kb-model-cache:/tmp/fastembed_cache
    restart: unless-stopped
```

- [ ] **Step 2: Declare the new volumes**

In the top-level `volumes:` block of the same file, add:
```yaml
  kb-contributed:
  kb-model-cache:
```

- [ ] **Step 3: Add the credentials to .env**

Add to `apps/ai-platform/librechat/.env` (gitignored) using the key from Task 2 Step 5:
```
AWS_ACCESS_KEY_ID=<from task 2>
AWS_SECRET_ACCESS_KEY=<from task 2>
```

- [ ] **Step 4: Rebuild and verify the wiring**

Run:
```bash
docker compose -p chat -f apps/ai-platform/librechat/docker-compose.yml up -d --build kb-mcp-server
docker exec kb-mcp-server sh -c 'ls -ld /knowledge/contributed && touch /knowledge/contributed/.writable && rm /knowledge/contributed/.writable && echo WRITABLE'
docker exec kb-mcp-server python -c "
from kb_mcp.config import settings
print('bucket:', settings.s3_bucket, '| roles:', settings.contributor_roles, '| cap:', settings.kb_max_upload_mb)
"
```
Expected: `WRITABLE`, then `bucket: quench-manthan-kb | roles: ('kb-contributor', 'ADMIN') | cap: 25`.

- [ ] **Step 5: Verify S3 reachability from the container**

Run:
```bash
docker exec kb-mcp-server python -c "
import boto3
c = boto3.client('s3', region_name='ap-south-1')
print(c.list_objects_v2(Bucket='quench-manthan-kb', Prefix='knowledge/contributed/').get('KeyCount'))
"
```
Expected: `0` (empty prefix, no error). An error here means the key or policy from Task 2 is wrong.

- [ ] **Step 6: Commit**

```bash
git add apps/ai-platform/librechat/docker-compose.yml
git commit -m "feat(kb): mount writable contributed volume, model cache and S3 credentials"
```

---

### Task 16: LibreChat — identity headers and S3 file storage

**Files:**
- Modify: `apps/ai-platform/librechat/librechat.yaml`
- Modify: `apps/ai-platform/librechat/docker-compose.yml` (librechat service env)

- [ ] **Step 1: Add identity headers to the kb MCP server**

In `apps/ai-platform/librechat/librechat.yaml`, replace the `kb:` block under `mcpServers:`:
```yaml
  # Knowledge base. The headers are what make the write tools safe: LibreChat
  # substitutes these placeholders server-side from the authenticated session,
  # so the model has no channel to set them and cannot claim a role it was not
  # granted. Removing them does not open the tools up — kb-mcp-server refuses
  # any request with no identity.
  kb:
    type: streamable-http
    url: "http://kb-mcp-server:8106/mcp"
    timeout: 60000
    headers:
      X-User-Id: "{{LIBRECHAT_USER_ID}}"
      X-User-Email: "{{LIBRECHAT_USER_EMAIL}}"
      X-User-Role: "{{LIBRECHAT_USER_ROLE}}"
```

- [ ] **Step 2: Switch file storage to S3**

Add at the top level of `librechat.yaml`, after the `version:` line:
```yaml
# Chat attachments go to S3 so an uploaded document survives container
# recreation and is reachable by kb-mcp-server. /app/uploads was never a
# volume, so files written there were lost on every recreate.
fileStrategy: s3
```

Add to the `librechat` (api) service `environment:` block in `docker-compose.yml`:
```yaml
      - AWS_BUCKET_NAME=quench-manthan-kb
      - AWS_REGION=ap-south-1
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
```

- [ ] **Step 3: Restart and verify the config loaded**

Run:
```bash
docker compose -p chat -f apps/ai-platform/librechat/docker-compose.yml up -d librechat
until curl -sf -o /dev/null http://localhost/api/config; do sleep 3; done
docker exec noc-librechat sh -c 'grep -A4 "^  kb:" /app/librechat.yaml'
```
Expected: the `kb:` block with all three headers.

- [ ] **Step 4: Verify an upload now lands in S3**

Attach any small file in a chat, then:
```bash
aws s3 ls s3://quench-manthan-kb/chat-uploads/ --recursive
```
Expected: one object under `chat-uploads/<userId>/`. If empty, check `docker logs noc-librechat --tail 50` for S3 errors.

- [ ] **Step 5: Commit**

```bash
git add apps/ai-platform/librechat/librechat.yaml apps/ai-platform/librechat/docker-compose.yml
git commit -m "feat(chat): forward user identity to the KB server and store files in S3"
```

---

### Task 17: Grant the contributor role and attach the tools

**Files:** none — runtime configuration.

- [ ] **Step 1: Create the role**

The `roles` collection already holds `noc-l1`, `noc-engineer`, `noc-legacy`. Add the contributor role:
```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval '
if (!db.roles.findOne({name:"kb-contributor"})) {
  const base = db.roles.findOne({name:"noc-engineer"});
  const doc = Object.assign({}, base, {name:"kb-contributor"});
  delete doc._id;
  db.roles.insertOne(doc);
}
print(db.roles.find({},{_id:0,name:1}).toArray().map(r=>r.name).join(", "));
'
```
Expected: the list now includes `kb-contributor`.

- [ ] **Step 2: Attach the three new tools to the agent**

In LibreChat, open the **NOC Engineer (Kimi)** agent in Agent Builder and add `kb_upload_document`, `kb_remove_document`, `kb_list_documents` from the `kb` server. Then verify:
```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval '
const a = db.agents.findOne({name:"NOC Engineer (Kimi)"});
print((a.tools||[]).filter(t=>t.indexOf("kb")>-1).join("\n"));
'
```
Expected: four `kb_*` tool ids including the three new ones.

- [ ] **Step 3: Export the agent so the change is in git**

```bash
sh apps/ai-platform/librechat/agents/export-agents.sh
git add apps/ai-platform/librechat/agents/agents.json
git commit -m "chore(chat): attach KB upload tools to the NOC Engineer agent"
```

---

### Task 18: Qdrant snapshot to S3

**Files:**
- Create: `scripts/kb-snapshot.ps1`

- [ ] **Step 1: Write the script**

Create `scripts/kb-snapshot.ps1`:
```powershell
#!/usr/bin/env pwsh
# Snapshot the ev_kb Qdrant collection and upload it to S3.
#
# The index is derived and could be rebuilt by re-running ingest, but that
# re-embeds the whole corpus. A snapshot restores in seconds, which is the
# difference between a recoverable incident and an afternoon.
#
# Usage:  ./scripts/kb-snapshot.ps1
[CmdletBinding()]
param(
    [string]$Collection = 'ev_kb',
    [string]$Bucket = 'quench-manthan-kb'
)

$ErrorActionPreference = 'Stop'

Write-Host "Creating snapshot of '$Collection'..."
$created = docker exec qdrant sh -c "wget -qO- --post-data='' http://localhost:6333/collections/$Collection/snapshots"
if (-not $created) { throw "snapshot request returned nothing - is the qdrant container running?" }

$name = ($created | ConvertFrom-Json).result.name
if (-not $name) { throw "no snapshot name in response: $created" }
Write-Host "Snapshot: $name"

$local = Join-Path $env:TEMP $name
docker cp "qdrant:/qdrant/snapshots/$Collection/$name" $local

$stamp = Get-Date -Format 'yyyy-MM-dd'
aws s3 cp $local "s3://$Bucket/snapshots/$Collection/$stamp-$name"
Remove-Item $local -Force

# Keep the container's snapshot directory from growing without bound.
docker exec qdrant sh -c "rm -f /qdrant/snapshots/$Collection/$name"

Write-Host "Uploaded to s3://$Bucket/snapshots/$Collection/$stamp-$name"
```

- [ ] **Step 2: Run it**

Run: `./scripts/kb-snapshot.ps1`
Expected: ends with `Uploaded to s3://quench-manthan-kb/snapshots/ev_kb/<date>-<name>`.

- [ ] **Step 3: Verify the object exists**

Run: `aws s3 ls s3://quench-manthan-kb/snapshots/ev_kb/`
Expected: one `.snapshot` object.

- [ ] **Step 4: Document the restore path**

Append to `services/mcp/kb/README.md`:
```markdown
## Backup and restore

`./scripts/kb-snapshot.ps1` snapshots the `ev_kb` collection and uploads it to
`s3://quench-manthan-kb/snapshots/ev_kb/`. Run it after a bulk ingest.

To restore, download the snapshot, copy it into the container, and ask Qdrant to
recover from it:

```
aws s3 cp s3://quench-manthan-kb/snapshots/ev_kb/<name> .
docker cp <name> qdrant:/qdrant/snapshots/ev_kb/<name>
docker exec qdrant sh -c "wget -qO- --post-data='{\"location\":\"file:///qdrant/snapshots/ev_kb/<name>\"}' \
  --header='Content-Type: application/json' http://localhost:6333/collections/ev_kb/snapshots/recover"
```

If no snapshot exists, the collection is still rebuildable from the corpus:
`docker exec kb-mcp-server python -m kb_mcp.ingest --prune`. That re-embeds every
document, so it is slower but loses nothing.
```

- [ ] **Step 5: Commit**

```bash
git add scripts/kb-snapshot.ps1 services/mcp/kb/README.md
git commit -m "feat(kb): snapshot the vector collection to S3 with a documented restore"
```

---

### Task 19: Live end-to-end verification

**Files:** none — verification against the running stack.

- [ ] **Step 1: Grant yourself the contributor role**

```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval '
db.users.updateOne({email:"noc-tester@quench.local"},{$set:{role:"kb-contributor"}});
db.users.find({},{_id:0,email:1,role:1}).forEach(u=>print(u.email+" -> "+u.role));
'
```

- [ ] **Step 2: Verify the tools are live on the server**

Write this helper and run it inside the container:
```bash
docker exec -i kb-mcp-server sh -c 'cat > /tmp/kbtools.py' <<'PY'
import json, urllib.request
URL = "http://localhost:8106/mcp"

def post(payload, sid=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if sid:
        h["Mcp-Session-Id"] = sid
    r = urllib.request.urlopen(urllib.request.Request(URL, json.dumps(payload).encode(), h), timeout=60)
    return r.headers.get("Mcp-Session-Id"), r.read().decode()

sid, _ = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
               "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                          "clientInfo": {"name": "cli", "version": "0"}}})
post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)
_, body = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, sid)
for line in body.splitlines():
    if line.startswith("data: "):
        print(sorted(t["name"] for t in json.loads(line[6:])["result"]["tools"]))
PY
docker exec kb-mcp-server python //tmp/kbtools.py
```
Expected: `['kb_list_documents', 'kb_remove_document', 'kb_search_noc', 'kb_upload_document']`.

If only `kb_search_noc` appears, `build_writer` returned `None` — check
`docker logs kb-mcp-server` for the "upload tools disabled" warning, which names the reason.

- [ ] **Step 3: Upload a real document from chat**

As `noc-tester@quench.local`, attach a small `.docx` containing a distinctive sentence (for example "The Pune depot uses connector lock revision 7B"), then ask: *"Add this document to the knowledge base."*

Expected: the agent reports the document is now searchable, with a chunk count.

- [ ] **Step 4: Verify each layer independently**

```bash
aws s3 ls s3://quench-manthan-kb/knowledge/contributed/
docker exec kb-mcp-server sh -c 'ls -l /knowledge/contributed/'
docker exec kb-mcp-server python -c "
import urllib.request, json
i = json.load(urllib.request.urlopen('http://qdrant:6333/collections/ev_kb'))['result']
print('points:', i['points_count'])
"
```
Expected: the object in S3, the file in the container, and a points count higher than 190.

- [ ] **Step 5: Search for it**

In chat: *"What connector lock revision does the Pune depot use?"*
Expected: the answer cites `contributed/<filename>`.

- [ ] **Step 6: Verify the permission gate with a real denial**

```bash
docker exec noc-librechat-mongo mongosh LibreChat --quiet --eval '
db.users.updateOne({email:"noc-tester@quench.local"},{$set:{role:"noc-engineer"}})'
```
Then attach another file in chat and ask to add it.
Expected: a refusal naming the required role. Check `docker logs kb-mcp-server --tail 20` for the `denied ... role=` line.

**This is the test that matters most** — it proves the gate holds against a real caller, not just a unit test.

- [ ] **Step 7: Verify removal**

Restore the contributor role, then in chat: *"List the documents in the knowledge base"*, followed by *"Remove contributed/&lt;filename&gt;"*.
Expected: removal confirmed; a repeat search no longer returns it; `aws s3 ls s3://quench-manthan-kb/knowledge/contributed/` no longer lists it.

- [ ] **Step 8: Verify no regression on curated content**

In chat: *"What does OCPP error code ConnectorLockFailure mean?"*
Expected: an answer citing an `ev-core/` source, proving the curated corpus still resolves.

- [ ] **Step 9: Record the results**

Append a short "Live verification 2026-08-23" section to `services/mcp/kb/README.md` listing which of §9's criteria passed, then commit.

---

### Task 20: Release

**Files:**
- Modify: `VERSION`, `CHANGELOG.md`, `apps/ai-platform/librechat/librechat.yaml` (via the bump script)

- [ ] **Step 1: Run the full suite one last time**

Run: `cd services/mcp/kb && python -m pytest -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 2: Bump the minor version**

This is a feature, so `minor`:
```bash
./scripts/bump-version.ps1 minor
```
Expected: `Bumping 0.12.2 -> 0.13.0`, then `Committed + tagged v0.13.0`.

- [ ] **Step 3: Restart LibreChat to pick up the new welcome line**

```bash
docker compose -p chat -f apps/ai-platform/librechat/docker-compose.yml up -d librechat
until curl -sf -o /dev/null http://localhost/api/config; do sleep 3; done
echo UP
```

- [ ] **Step 4: Push**

```bash
git push && git push origin v0.13.0
```

---

## Self-Review

**Spec coverage** — every section maps to a task:

| Spec | Task |
|---|---|
| §3.2 identity mechanism | 6, 12, 16 |
| §4.1 storage layout | 2, 15 |
| §4.2 component boundaries | 6, 7, 8, 11 |
| §4.3 contributed scope | 14 |
| §4.5 four tools | 11, 12 |
| §4.6 flow | 11, 19 |
| §4.7 document formats | 3, 4, 5 |
| §4.8 vector store + durability | 15 (model cache), 18 (snapshots) |
| §5 configuration | 10, 15, 16 |
| §6 error handling | 11 (every row has a test in Task 11) |
| §7 testing | 3–13 |
| §8 risks | 1 (risk 1), 2 (risk 2), 20 (risk 3), 2 step 3 (risk 4) |
| §9 verification criteria | 19 |

**Type consistency checked:** `ingest_file(path, source, scope_name, store, embedder, *, max_chars, overlap, doc_hash, extra_payload)` is defined in Task 8 and called with those exact keywords in Tasks 8 and 11. `UploadRef(key, filename, size)` is defined in Task 7 and consumed in Task 11. `KbWriter(...)` keyword arguments match between Tasks 11, 12 and 13. `list_documents(scopes=...)` is defined in Task 9 and called in Tasks 11 and 12. `chunk_tabular(text, max_chars, overlap_chars)` is defined in Task 5 and used in Task 8.

**Known ordering constraint:** Task 8 imports `TABULAR` from `parse.py`, which Task 8 Step 4 adds. Tasks 3–5 must complete before Task 8.
