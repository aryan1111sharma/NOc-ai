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
