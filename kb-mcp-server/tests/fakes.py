"""Deterministic test doubles: a fake embedder + in-memory Qdrant helper."""
import hashlib


class FakeEmbedder:
    """Deterministic small-dim embeddings so tests need no model download."""
    dim = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([b / 255.0 for b in h[: self.dim]])
        return out
