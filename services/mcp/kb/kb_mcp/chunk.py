"""Boundary-aware text chunking with character overlap (pure, dependency-free)."""


def chunk_text(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    cur = ""
    for p in paras:
        if cur and len(cur) + 2 + len(p) > max_chars:
            chunks.append(cur)
            tail = cur[-overlap_chars:] if overlap_chars else ""
            cur = (tail + "\n\n" + p).strip()
        else:
            cur = (cur + "\n\n" + p).strip() if cur else p
        while len(cur) > max_chars:
            chunks.append(cur[:max_chars])
            cur = cur[max_chars - overlap_chars:] if overlap_chars else cur[max_chars:]
    if cur.strip():
        chunks.append(cur.strip())
    return chunks
