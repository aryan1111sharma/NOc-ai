"""S3 access for the contributed corpus.

Every AWS call the service makes lives here, so the ingest pipeline stays a
pure filesystem walker and the tests need no network. The client is injected,
never constructed at import time.
"""
from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path

CHAT_PREFIX = "chat-uploads"
CORPUS_PREFIX = "knowledge/contributed"


class S3Error(RuntimeError):
    """An S3 operation failed. Never swallowed — a partial index is worse
    than a loud failure."""


@dataclass(frozen=True)
class UploadRef:
    key: str
    filename: str
    size: int


def _original_name(key: str) -> str:
    """LibreChat stores `<uuid>__<original name>`; recover the original."""
    base = key.rsplit("/", 1)[-1]
    return base.split("__", 1)[1] if "__" in base else base


class S3Corpus:
    def __init__(self, client, bucket: str):
        self.c, self.bucket = client, bucket

    def _list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.c.list_objects_v2(**kwargs)
            out.extend(resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return out
            token = resp.get("NextContinuationToken")
            if not token:
                # A well-behaved S3 always pairs IsTruncated=True with a
                # token. Without one the next call would just repeat this
                # one forever — fail loudly instead of spinning.
                raise S3Error(
                    f"S3 reported IsTruncated=True for prefix {prefix!r} but "
                    "returned no NextContinuationToken"
                )

    def corpus_key(self, filename: str) -> str:
        """Caller-supplied names become keys only here, so this is the one
        place traversal has to be stopped. Flatten to a basename: a name like
        `../../evil.md` must not write outside the corpus prefix."""
        safe = posixpath.basename(filename.replace("\\", "/")).strip()
        if not safe or safe in (".", ".."):
            raise S3Error(f"unusable filename: {filename!r}")
        return f"{CORPUS_PREFIX}/{safe}"

    def find_user_upload(self, user_id: str, filename: str) -> UploadRef | None:
        """Newest matching upload under this user's own prefix, or None.

        Scoped to `chat-uploads/<user_id>/` so one contributor cannot reach
        another's file by guessing its name.
        """
        wanted = filename.strip().casefold()
        matches = []
        for obj in self._list(f"{CHAT_PREFIX}/{user_id}/"):
            name = _original_name(obj["Key"])
            if name.casefold() == wanted:
                matches.append((obj["LastModified"], obj["Key"], obj["Size"], name))
        if not matches:
            return None
        matches.sort(reverse=True)
        _, key, size, name = matches[0]
        return UploadRef(key=key, filename=name, size=size)

    def copy_to_corpus(self, ref: UploadRef) -> str:
        key = self.corpus_key(ref.filename)
        try:
            self.c.copy_object(
                Bucket=self.bucket, Key=key,
                CopySource={"Bucket": self.bucket, "Key": ref.key},
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise S3Error(f"could not copy {ref.key} into the corpus: {exc}") from exc
        return key

    def download_corpus(self, dest: Path) -> list[Path]:
        """Mirror the corpus prefix into `dest`. Returns the files written."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        for obj in self._list(f"{CORPUS_PREFIX}/"):
            name = obj["Key"].rsplit("/", 1)[-1]
            if not name:
                continue
            try:
                body = self.c.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
            except Exception as exc:  # noqa: BLE001
                raise S3Error(f"could not read {obj['Key']}: {exc}") from exc
            path = dest / name
            path.write_bytes(body)
            written.append(path)
        return written

    def download_one(self, key: str, dest: Path) -> Path:
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            body = self.c.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception as exc:  # noqa: BLE001
            raise S3Error(f"could not read {key}: {exc}") from exc
        dest.write_bytes(body)
        return dest

    def delete_from_corpus(self, filename: str) -> bool:
        key = self.corpus_key(filename)
        existing = {o["Key"] for o in self._list(f"{CORPUS_PREFIX}/")}
        if key not in existing:
            return False
        self.c.delete_object(Bucket=self.bucket, Key=key)
        return True
