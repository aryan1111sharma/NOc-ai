"""Parse a document into segments: [{text, location}]. location = page/heading hint."""
import re
from pathlib import Path

SUPPORTED = {".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm"}


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


def _parse_docx(path: Path) -> list[dict]:
    """Paragraphs and table cells, tagged with the nearest preceding heading.

    Word has no pages until it is rendered, so `location` uses the document's
    own structure instead: a heading is the most useful thing to cite back.
    """
    import docx  # imported lazily: a parser dependency must not slow server start

    doc = docx.Document(str(path))
    segs: list[dict] = []
    current = path.name
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if para.style is not None and (para.style.name or "").startswith("Heading"):
            current = text
            continue
        segs.append({"text": text, "location": current})
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            segs.append({"text": "\n".join(rows), "location": current})
    return segs


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
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
