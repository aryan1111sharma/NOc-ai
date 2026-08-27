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


#: Extensions the indexer will turn into text. Upload has its own, narrower allowlist
#: (`manage.UPLOADABLE`) — an upload must additionally survive transport.
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

#: Row-oriented formats whose chunks must carry the header line (see chunk_tabular).
#: Every format rendered by `_rows_to_text` belongs here: the renderer puts the header
#: on line 1 and the chunker re-prepends it, so a format left out of this set would
#: split mid-grid and retrieve as noise.
TABULAR = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv"}

#: Binary formats: uploaded base64-encoded, bytes decoded before text extraction.
BINARY = {".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".pptx", ".rtf"}

#: A per-sheet row cap so one pathological sheet cannot balloon the corpus. Rows beyond
#: it are dropped with a note in the segment, which is honest; silently truncating a
#: spreadsheet leaves the KB confidently wrong about "how many" questions.
MAX_ROWS_PER_SHEET = 5000


class ParseError(ValueError):
    """The file has the right extension but cannot be read (corrupt, wrong format,
    password-protected). Raised rather than returning [] so the caller can tell an
    "empty document" from an "unreadable document"."""


#: The corpus walk catches this name (see ingest.ingest_file). It is the same condition
#: ParseError describes — one class, two names — so a parser raises once and both the
#: bulk-ingest path and the upload path recognise it.
DocumentUnreadable = ParseError


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()



def _rows_to_text(rows: list[list[str]]) -> str:
    """Pipe-join cells and keep the header on its own first line.

    The header is what makes a row of values mean anything, so it is retained
    verbatim; the tabular chunker re-prepends it when a sheet spans chunks. That is
    why this returns single-newline lines and not blank-line-separated paragraphs:
    `chunk_tabular` splits on lines and treats line 1 as the header.

    Rows past MAX_ROWS_PER_SHEET are dropped with a visible note rather than silently,
    so a truncated sheet cannot leave the KB confidently wrong about a "how many"
    question.
    """
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    truncated = len(body) > MAX_ROWS_PER_SHEET
    if truncated:
        body = body[:MAX_ROWS_PER_SHEET]
    lines = [" | ".join(header)] + [" | ".join(cells) for cells in body]
    if truncated:
        lines.append(f"(only the first {MAX_ROWS_PER_SHEET} rows are indexed)")
    return "\n".join(lines)


def _cell_text(value, formula) -> str:
    """Cached value first, falling back to the formula text.

    A cell whose formula was never evaluated has no cached value; indexing it
    as blank loses the column silently, so the formula itself is kept — it is
    at least searchable and visibly uncalculated.
    """
    if value is not None:
        return str(value).strip()
    if isinstance(formula, str) and formula.startswith("="):
        return formula.strip()
    return ""


def _cell(value) -> str:
    """Render a cell that arrived already evaluated (xlrd, csv, docx, pptx).

    None becomes empty rather than 'None', which would otherwise be embedded as a real
    token and match queries containing the word.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _trim_empty_columns(rows: list[list[str]]) -> list[list[str]]:
    """Drop trailing columns that are empty in every row.

    read_only mode reports the used range inflated by any cell ever written
    to, even one later cleared, so a two-row table can arrive padded to
    hundreds of columns. Left in place, that padding crowds genuine content
    out of the chunk window.
    """
    width = 0
    for row in rows:
        for i, cell in enumerate(row):
            if cell:
                width = max(width, i + 1)
    return [row[:width] for row in rows] if width else []


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
    """Paragraphs and table cells, tagged with the nearest preceding heading.

    Word has no pages until it is rendered, so `location` uses the document's own
    structure instead: a heading is the most useful thing to cite back.

    The body is walked in document order rather than through `doc.paragraphs` and
    `doc.tables`. Those are two independent collections, so iterating them separately
    loses the interleaving and tags every table with whichever heading happened to come
    last in the file — which would cite a fault table against the wrong procedure.

    Prose accumulates into one segment per heading so a chunk does not straddle
    unrelated sections, while a table is emitted as its own segment: it is row-oriented
    text and gluing it onto the prose around it would bury both.
    """
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
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

    for child in d.element.body.iterchildren():
        if child.tag.endswith("}p"):
            para = Paragraph(child, d)
            t = para.text.strip()
            if not t:
                continue
            style = getattr(para, "style", None)
            if style is not None and (style.name or "").lower().startswith("heading"):
                flush()
                heading = t
            buf.append(t)
        elif child.tag.endswith("}tbl"):
            flush()
            rows = []
            for row in Table(child, d).rows:
                # A horizontally merged cell is repeated once per column it spans;
                # emitting it each time duplicates text into the embedding, so keep
                # only the first appearance.
                cells, previous = [], None
                for cell in row.cells:
                    if previous is not None and cell._tc is previous:
                        continue
                    previous = cell._tc
                    cells.append(_cell(cell.text))
                if any(cells):
                    rows.append(cells)
            rows = _trim_empty_columns(rows)
            if rows:
                segs.append({"text": _rows_to_text(rows), "location": heading})
    flush()
    return segs


def _parse_xlsx(path: Path) -> list[dict]:
    """One segment per sheet, rows pipe-joined, sheet name as the location.

    Two workbooks are opened, not one. `data_only=True` yields a formula's
    cached result, but a workbook generated programmatically was never
    rendered by Excel and has no cache — every formula cell would come back
    None and be silently indexed as blank. The second, formula-preserving
    load supplies the formula text so a cell is never silently empty.
    """
    from openpyxl import load_workbook  # lazy: keeps server start fast

    # Both opens sit inside the try: if the second one fails — a truncated
    # file, a memory error on a huge sheet — the first is already open, and
    # opening it outside would leak its handle past the finally.
    values = formulas = None
    try:
        values = load_workbook(str(path), read_only=True, data_only=True)
        formulas = load_workbook(str(path), read_only=True, data_only=False)
        segs = []
        for sheet, formula_sheet in zip(values.worksheets, formulas.worksheets):
            rows = []
            for value_row, formula_row in zip(
                sheet.iter_rows(values_only=True),
                formula_sheet.iter_rows(values_only=True),
            ):
                cells = [_cell_text(v, f) for v, f in zip(value_row, formula_row)]
                if any(cells):
                    rows.append(cells)
            rows = _trim_empty_columns(rows)
            if rows:
                segs.append({"text": _rows_to_text(rows), "location": sheet.title})
        return segs
    finally:
        for workbook in (values, formulas):
            if workbook is not None:
                workbook.close()


def _parse_xls(path: Path) -> list[dict]:
    """Legacy BIFF workbooks. openpyxl cannot read these and xlrd 2.x reads only these,
    so the two Excel branches share a renderer but never a library."""
    try:
        import xlrd
    except ImportError as e:  # pragma: no cover - dependency is declared
        raise ParseError(f"XLS support unavailable: {e}") from e
    try:
        book = xlrd.open_workbook(str(path))
    except Exception as e:
        raise ParseError(f"cannot open legacy .xls workbook: {e}") from e

    segs: list[dict] = []
    for sheet in book.sheets():
        rows = [[_cell(v) for v in sheet.row_values(i)] for i in range(sheet.nrows)]
        rows = _trim_empty_columns([r for r in rows if any(r)])
        if rows:
            segs.append({"text": _rows_to_text(rows), "location": sheet.name})
    return segs


def _parse_delimited(path: Path, raw: str) -> list[dict]:
    """CSV and TSV. The delimiter is sniffed rather than assumed, because an export
    named .csv is routinely semicolon- or tab-separated depending on the locale that
    produced it, and guessing wrong collapses every row into a single column."""
    delimiter = "\t" if path.suffix.lower() == ".tsv" else None
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(raw[:8192], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = [
        [_cell(c) for c in row]
        for row in csv.reader(io.StringIO(raw), delimiter=delimiter)
        if any(str(c).strip() for c in row)
    ]
    rows = _trim_empty_columns(rows)
    return [{"text": _rows_to_text(rows), "location": path.name}] if rows else []


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
    except ImportError as e:  # pragma: no cover - dependency is declared
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
                rows = _trim_empty_columns(
                    [[_cell(c.text) for c in r.cells] for r in shape.table.rows]
                )
                if rows:
                    parts.append(_rows_to_text(rows))
        if parts:
            segs.append({"text": "\n\n".join(parts), "location": f"slide {n}"})
    return segs


def _parse_rtf(path: Path) -> list[dict]:
    try:
        from striprtf.striprtf import rtf_to_text
    except ImportError as e:  # pragma: no cover - dependency is declared
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

    # utf-8-sig transparently strips an Excel BOM and still reads plain UTF-8.
    # Without it a .csv exported from Excel keeps the BOM glued to its first header
    # cell, so that column silently fails to match any query naming it.
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if ext in (".csv", ".tsv"):
        return _parse_delimited(path, raw)
    if ext == ".json":
        return _parse_json(path, raw)
    if ext == ".xml":
        return _parse_xml(path, raw)
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
