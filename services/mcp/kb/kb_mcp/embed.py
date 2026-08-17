"""Local embedding via fastembed (ONNX/CPU). No network at query time after model cache."""
from typing import Protocol


class Embedder(Protocol):
    dim: int
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastembedEmbedder:
    def __init__(self, model_name: str, dim: int):
        from fastembed import TextEmbedding  # imported lazily so tests don't need it
        self._model = TextEmbedding(model_name=model_name)
        self.dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return [vec.tolist() for vec in self._model.embed(texts)]
