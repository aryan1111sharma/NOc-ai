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


def chunk_tabular(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    """Chunk row-oriented text, repeating the header line on every chunk.

    A spreadsheet row means nothing without its column names: "E42 | 3 | 17"
    embeds to a vector that matches no plausible question. Repeating the
    header costs a few characters per chunk and makes every chunk
    independently interpretable.

    `overlap_chars` is accepted for signature parity with `chunk_text`, so the
    two are interchangeable at the call site, but it is deliberately unused:
    every chunk already repeats the header, which is the context an overlap
    would otherwise have to carry.
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
        if len(row) > budget:
            # A row wider than the whole budget cannot share a chunk with
            # anything. Flush what is pending, then hard-split it: the
            # max_chars ceiling is what keeps a chunk inside the embedding
            # model's window, and an over-long chunk is truncated by the
            # model rather than by us — silently losing the tail.
            if cur:
                chunks.append(header + "\n" + "\n".join(cur))
                cur, size = [], 0
            for i in range(0, len(row), budget):
                chunks.append(header + "\n" + row[i:i + budget])
            continue
        if cur and size + len(row) + 1 > budget:
            chunks.append(header + "\n" + "\n".join(cur))
            cur, size = [], 0
        cur.append(row)
        size += len(row) + 1
    if cur:
        chunks.append(header + "\n" + "\n".join(cur))
    return chunks
