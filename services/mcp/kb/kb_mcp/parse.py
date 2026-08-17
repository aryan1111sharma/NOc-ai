"""Parse a document into segments: [{text, location}]. location = page/heading hint."""
import re
from pathlib import Path

SUPPORTED = {".pdf", ".md", ".markdown", ".txt", ".html", ".htm"}


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+\n", "\n", re.sub(r"[ \t]+", " ", text)).strip()


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
    raw = path.read_text(encoding="utf-8", errors="replace")
    if ext in (".html", ".htm"):
        raw = _strip_html(raw)
    return [{"text": raw.strip(), "location": path.name}] if raw.strip() else []
