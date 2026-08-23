"""Parse a document into segments: [{text, location}]. location = page/heading hint."""
import re
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".xlsx", ".csv", ".md", ".markdown", ".txt", ".html", ".htm"}


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


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


def _rows_to_text(rows: list[list[str]]) -> str:
    """Pipe-join cells and keep the header on its own first line.

    The header is what makes a row of values mean anything, so it is retained
    verbatim; the tabular chunker re-prepends it when a sheet spans chunks.
    """
    return "\n".join(" | ".join(cells) for cells in rows)


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


def _parse_csv(path: Path) -> list[dict]:
    import csv

    # utf-8-sig transparently strips an Excel BOM and still reads plain UTF-8.
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as fh:
        rows = [[c.strip() for c in row] for row in csv.reader(fh) if any(c.strip() for c in row)]
    return [{"text": _rows_to_text(rows), "location": path.name}] if rows else []


def parse_file(path: Path) -> list[dict]:
    ext = path.suffix.lower()
    if ext not in SUPPORTED:
        return []
    if ext == ".pdf":
        import fitz  # PyMuPDF
        segs = []
        with fitz.open(path) as doc:
            for page in doc:
                t = page.get_text().strip()
                if t:
                    segs.append({"text": t, "location": f"p.{page.number + 1}"})
        return segs
    if ext == ".docx":
        return _parse_docx(path)
    if ext == ".xlsx":
        return _parse_xlsx(path)
    if ext == ".csv":
        return _parse_csv(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
