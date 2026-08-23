"""Deterministic test doubles: a fake embedder + in-memory Qdrant helper."""
import hashlib
from datetime import datetime, timedelta, timezone


class FakeEmbedder:
    """Deterministic small-dim embeddings so tests need no model download."""
    dim = 8

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            h = hashlib.sha256(t.encode("utf-8")).digest()
            out.append([b / 255.0 for b in h[: self.dim]])
        return out


class FakeS3:
    """In-memory stand-in for the five boto3 S3 calls kb_mcp.s3 makes."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.times: dict[str, datetime] = {}
        self._clock = datetime(2026, 8, 23, tzinfo=timezone.utc)

    def put(self, key: str, data: bytes, age_minutes: int = 0) -> None:
        """Test helper — seeds an object. Not part of the boto3 surface."""
        self.objects[key] = data
        self.times[key] = self._clock - timedelta(minutes=age_minutes)

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [
                {"Key": k, "Size": len(self.objects[k]), "LastModified": self.times[k]}
                for k in keys
            ],
            "IsTruncated": False,
        }

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        payload = self.objects[Key]

        class _Body:
            @staticmethod
            def read():
                return payload

        return {"Body": _Body(), "ContentLength": len(payload)}

    def copy_object(self, Bucket, Key, CopySource):
        src = CopySource["Key"] if isinstance(CopySource, dict) else CopySource
        self.put(Key, self.objects[src])

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)
        self.times.pop(Key, None)
