"""Parse a document into segments: [{text, location}]. location = page/heading hint.

Every parser here answers to one consumer: `chunk.chunk_text`, which splits on blank
lines. So each parser's job is not "extract the characters" but "emit text whose blank
lines fall where a retrieval boundary should fall". That is why tabular formats below
render one record per paragraph rather than dumping a grid — a chunk that ends mid-row
retrieves as noise.

Segments carry a `location` because it becomes the citation the agent shows ("p.4",
"sheet 'Q3 Sales' rows 2-41"). A location that cannot be pointed at in the original file
is worse than none, so each parser emits the most specific locator that format offers.
"""
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

#: Extensions the indexer can turn into text. Upload has its own, narrower allowlist
#: (`manage.UPLOADABLE`) because upload additionally has to survive the transport.
SUPPORTED = {
    ".pdf",
    ".md", ".markdown", ".txt",
    ".html", ".htm",
    ".docx",
    ".xlsx", ".xlsm", ".xls",
    ".csv", ".tsv",
    ".pptx",
    ".json",
    ".xml",
    ".rtf",
}

#: Binary formats: uploaded base64-encoded, and read as bytes rather than decoded text.
BINARY = {".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".pptx", ".rtf"}

#: A cell/row cap so one pathological sheet cannot balloon the corpus. Rows beyond this
#: are dropped with a note in the segment, which is honest; silently truncating a
#: spreadsheet would make the KB confidently wrong about "how many" questions.
MAX_ROWS_PER_SHEET = 5000


class ParseError(ValueError):
    """The file is the right extension but cannot be read (corrupt, wrong format,
    password-protected). Raised rather than returning [] so the caller can tell
    "empty document" apart from "unreadable document"."""


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _cell(value) -> str:
    """Render a cell for retrieval. None becomes empty rather than 'None', which would
    otherwise be embedded as a real token and match queries containing the word."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _records(rows, sheet_label: str, source_label: str) -> list[dict]:
    """Shared renderer for every tabular format (xlsx, xls, csv, tsv, docx tables).

    Emits a schema paragraph followed by one paragraph per row, each row repeating its
    column names:

        Widgets Q3 — sheet "Sales" (3 columns, 2 rows)
        Columns: Region, Product, Revenue

        sheet "Sales" row 2 | Region: North | Product: Widget A | Revenue: 12345

    Repeating headers on every row is deliberate redundancy. Chunks are retrieved in
    isolation, so a row that reads "North | Widget A | 12345" without its headers is
    unanswerable — the embedding has no idea 12345 is revenue.
    """
    rows = [r for r in rows if any(_cell(c) for c in r)]
    if not rows:
        return []

    header = [_cell(c) for c in rows[0]]
    body = rows[1:]
    truncated = False
    if len(body) > MAX_ROWS_PER_SHEET:
        body = body[:MAX_ROWS_PER_SHEET]
        truncated = True

    # A header row is only usable if it is mostly non-empty labels; otherwise treat the
    # whole sheet as headerless data and fall back to positional column names.
    if sum(1 for h in header if h) < max(1, len(header) // 2):
        header = [f"column {i + 1}" for i in range(len(header))]
        body = rows

    preamble = (
        f'{source_label} — {sheet_label} ({len(header)} columns, {len(body)} rows)\n'
        f'Columns: {", ".join(h for h in header if h)}'
    )
    if truncated:
        preamble += f"\n(only the first {MAX_ROWS_PER_SHEET} rows are indexed)"

    paragraphs = [preamble]
    for offset, row in enumerate(body, start=2):
        pairs = [
            f"{header[i]}: {_cell(v)}"
            for i, v in enumerate(row)
            if i < len(header) and _cell(v)
        ]
        if pairs:
            paragraphs.append(f'{sheet_label} row {offset} | ' + " | ".join(pairs))

    if len(paragraphs) == 1:
        return []
    first, last = 2, 2 + len(body) - 1
    return [{
        "text": "\n\n".join(paragraphs),
        "location": f"{sheet_label} rows {first}-{last}",
    }]


def _parse_pdf(path: Path) -> list[dict]:
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ParseError(f"cannot open PDF: {e}") from e
    segs, pages = [], 0
    with doc:
        for page in doc:
            pages += 1
            t = page.get_text().strip()
            if t:
                segs.append({"text": t, "location": f"p.{page.number + 1}"})
    if pages and not segs:
        # Every page rendered zero characters: an image-only (scanned) PDF. There is no
        # text layer to extract, and OCR is deliberately out of scope, so fail loudly
        # instead of indexing an empty document that would silently never retrieve.
        raise ParseError(
            "PDF has no extractable text layer (likely a scan). OCR is not supported; "
            "upload a text-based PDF or a converted document instead."
        )
    return segs


def _parse_docx(path: Path) -> list[dict]:
    try:
        import docx
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise ParseError(f"DOCX support unavailable: {e}") from e
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ParseError(f"cannot open DOCX: {e}") from e

    segs: list[dict] = []
    buf: list[str] = []
    heading = path.name

    def flush():
        if buf:
            segs.append({"text": "\n\n".join(buf), "location": heading})
            buf.clear()

    for p in d.paragraphs:
        t = p.text.strip()
        if not t:
            continue
        # Start a new segment at each heading so the location stays meaningful and
        # chunks do not straddle unrelated sections.
        if (p.style.name or "").lower().startswith("heading"):
            flush()
            heading = t
        buf.append(t)
    flush()

    for i, table in enumerate(d.tables, start=1):
        rows = [[c.text for c in r.cells] for r in table.rows]
        segs.extend(_records(rows, f'table {i}', path.name))
    return segs


def _parse_xlsx(path: Path) -> list[dict]:
    try:
        from openpyxl import load_workbook
    except ImportError as e:  # pragma: no cover
        raise ParseError(f"XLSX support unavailable: {e}") from e
    try:
        # data_only=True yields the last-calculated value of a formula rather than the
        # formula text: "=SUM(B2:B9)" is useless to retrieval, "41230" is the answer.
        wb = load_workbook(str(path), read_only=True, data_only=True)
    except Exception as e:
        raise ParseError(f"cannot open workbook: {e}") from e

    segs: list[dict] = []
    try:
        for ws in wb.worksheets:
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            segs.extend(_records(rows, f'sheet "{ws.title}"', f'workbook "{path.name}"'))
    finally:
        wb.close()
    return segs


def _parse_xls(path: Path) -> list[dict]:
    try:
        import xlrd
    except ImportError as e:  # pragma: no cover
        raise ParseError(f"XLS support unavailable: {e}") from e
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as e:
        raise ParseError(f"cannot open legacy .xls workbook: {e}") from e

    segs: list[dict] = []
    for sheet in book.sheets():
        rows = [sheet.row_values(i) for i in range(sheet.nrows)]
        segs.extend(_records(rows, f'sheet "{sheet.name}"', f'workbook "{path.name}"'))
    return segs


def _parse_delimited(path: Path, raw: str) -> list[dict]:
    delimiter = "\t" if path.suffix.lower() == ".tsv" else None
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = list(csv.reader(io.StringIO(raw), delimiter=delimiter))
    return _records(rows, "data", f'file "{path.name}"')


def _flatten_json(node, prefix: str, out: list[str]) -> None:
    """Render JSON as `path: value` lines. The path is kept because it carries the
    semantics — "invoice.customer.city: Pune" retrieves for a question about a customer
    city, while a bare "Pune" does not."""
    if isinstance(node, dict):
        for k, v in node.items():
            _flatten_json(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _flatten_json(v, f"{prefix}[{i}]", out)
    else:
        out.append(f"{prefix}: {node}")


def _parse_json(path: Path, raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ParseError(f"invalid JSON: {e}") from e
    lines: list[str] = []
    _flatten_json(data, "", lines)
    # Group into paragraphs so chunking has boundaries; 40 lines keeps related keys together.
    paras = ["\n".join(lines[i:i + 40]) for i in range(0, len(lines), 40)]
    return [{"text": p, "location": path.name} for p in paras if p.strip()]


def _parse_xml(path: Path, raw: str) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        raise ParseError(f"invalid XML: {e}") from e
    lines: list[str] = []

    def walk(el, prefix: str) -> None:
        tag = el.tag.split("}")[-1]  # drop namespace
        path_ = f"{prefix}.{tag}" if prefix else tag
        for k, v in el.attrib.items():
            lines.append(f"{path_}@{k.split('}')[-1]}: {v}")
        if (el.text or "").strip():
            lines.append(f"{path_}: {el.text.strip()}")
        for child in el:
            walk(child, path_)

    walk(root, "")
    paras = ["\n".join(lines[i:i + 40]) for i in range(0, len(lines), 40)]
    return [{"text": p, "location": path.name} for p in paras if p.strip()]


def _parse_pptx(path: Path) -> list[dict]:
    try:
        from pptx import Presentation
    except ImportError as e:  # pragma: no cover
        raise ParseError(f"PPTX support unavailable: {e}") from e
    try:
        prs = Presentation(str(path))
    except Exception as e:
        raise ParseError(f"cannot open presentation: {e}") from e

    segs: list[dict] = []
    for n, slide in enumerate(prs.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                t = shape.text_frame.text.strip()
                if t:
                    parts.append(t)
            if getattr(shape, "has_table", False):
                rows = [[c.text for c in r.cells] for r in shape.table.rows]
                for seg in _records(rows, f"slide {n} table", path.name):
                    parts.append(seg["text"])
        if parts:
            segs.append({"text": "\n\n".join(parts), "location": f"slide {n}"})
    return segs


def _parse_rtf(path: Path) -> list[dict]:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as e:  # pragma: no cover
        raise ParseError(f"RTF support unavailable: {e}") from e
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        text = rtf_to_text(raw, errors="ignore").strip()
    except Exception as e:
        raise ParseError(f"cannot read RTF: {e}") from e
    return [{"text": text, "location": path.name}] if text else []


def parse_file(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        return []

    if ext == ".pdf":
        return _parse_pdf(path)
    if ext == ".docx":
        return _parse_docx(path)
    if ext in (".xlsx", ".xlsm"):
        return _parse_xlsx(path)
    if ext == ".xls":
        return _parse_xls(path)
    if ext == ".pptx":
        return _parse_pptx(path)
    if ext == ".rtf":
        return _parse_rtf(path)

    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".csv", ".tsv"):
        return _parse_delimited(path, raw)
    if ext == ".json":
        return _parse_json(path, raw)
    if ext == ".xml":
        return _parse_xml(path, raw)
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
