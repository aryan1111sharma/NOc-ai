"""S3 access for the contributed corpus.

Every AWS call the service makes lives here, so the ingest pipeline stays a
pure filesystem walker and the tests need no network. The client is injected,
never constructed at import time.
"""
from __future__ import annotations

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


_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


def _error_code(exc: Exception) -> str:
    """Pull an S3 error code out of a botocore exception without importing
    botocore — this module is deliberately client-agnostic so the tests can
    inject a fake."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    error = response.get("Error")
    return str(error.get("Code", "")) if isinstance(error, dict) else ""


class S3Corpus:
    def __init__(self, client, bucket: str):
        self.c, self.bucket = client, bucket

    def _list(self, prefix: str) -> list[dict]:
        out, token = [], None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            try:
                resp = self.c.list_objects_v2(**kwargs)
            except Exception as exc:  # noqa: BLE001
                raise S3Error(f"could not list {prefix!r}: {exc}") from exc
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

    def _exists(self, key: str) -> bool:
        """True/False only when S3 actually answered.

        A failure that is not a recognised not-found must not read as
        "absent": that would tell a caller their document was already gone
        when it exists, and report an overwrite as a fresh write — wrong
        answers delivered confidently, exactly when S3 is misbehaving.
        """
        try:
            self.c.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception as exc:  # noqa: BLE001 - narrowed immediately below
            if _error_code(exc) in _NOT_FOUND_CODES:
                return False
            raise S3Error(f"could not check whether {key} exists: {exc}") from exc

    def corpus_key(self, filename: str) -> str:
        """Caller-supplied names become keys only here, so this is the one
        place traversal has to be stopped.

        A name containing a separator is rejected rather than flattened to its
        basename. Flattening looks safer than it is: `delete_from_corpus`
        would resolve "2026/report.md" to "report.md", delete that different
        document, and report success. Every legitimate name arriving here is
        already a single path segment, so rejection costs nothing.
        """
        name = filename.strip()
        if not name or name in (".", ".."):
            raise S3Error(f"unusable filename: {filename!r}")
        if "/" in name or "\\" in name:
            raise S3Error(
                f"{filename!r} is a path, not a filename — documents live directly "
                f"under {CORPUS_PREFIX}/ and a name containing a separator would "
                "resolve to a different document"
            )
        if any(ch < " " for ch in name):
            # Control characters (NUL, CR, LF, ...) are legal in an S3 key
            # but fatal on the filesystem later — reject here, not at write time.
            raise S3Error(f"filename contains a control character: {filename!r}")
        return f"{CORPUS_PREFIX}/{name}"

    def find_user_upload(self, user_id: str, filename: str) -> UploadRef | None:
        """Newest matching upload under this user's own prefix, or None.

        Scoped to `chat-uploads/<user_id>/` so one contributor cannot reach
        another's file by guessing its name. The trailing slash on the prefix
        is load-bearing: without it, user "u1" would also match keys under
        "chat-uploads/u1extra/", "chat-uploads/u10/", and so on.
        """
        wanted = filename.strip().casefold()
        if not wanted:
            # An empty/blank request would otherwise match a zero-byte
            # folder-marker object (key ending in "/", whose recovered name
            # is "") and return a bogus UploadRef for it.
            return None
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

    def copy_to_corpus(self, ref: UploadRef) -> tuple[str, bool]:
        """Copy an upload into the corpus. Returns (key, replaced_existing).

        The corpus namespace is flat and global while uploads are per-user, so
        two contributors who both attach "notes.md" collapse onto one key. The
        caller is told when that happens so a person can be warned, rather
        than one document quietly disappearing.
        """
        key = self.corpus_key(ref.filename)
        replaced = self._exists(key)
        try:
            self.c.copy_object(
                Bucket=self.bucket, Key=key,
                CopySource={"Bucket": self.bucket, "Key": ref.key},
            )
        except Exception as exc:  # noqa: BLE001 - re-raised with context
            raise S3Error(f"could not copy {ref.key} into the corpus: {exc}") from exc
        return key, replaced

    def download_corpus(self, dest: Path) -> list[Path]:
        """Mirror the corpus prefix into `dest`, preserving any sub-path
        under the prefix. Returns the files written.

        Nothing this module writes ever produces a key with a sub-path under
        CORPUS_PREFIX (corpus_key rejects separators), but an out-of-band
        actor could place one directly in S3. Mirroring the sub-path avoids
        silently colliding two same-named files from different sources, and
        the resolved-path check stops such a key from writing outside `dest`.
        """
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        dest_root = dest.resolve()
        root_prefix = f"{CORPUS_PREFIX}/"
        written = []
        for obj in self._list(root_prefix):
            relative = obj["Key"][len(root_prefix):]
            if not relative or relative.endswith("/"):
                continue  # folder marker, not a document
            try:
                body = self.c.get_object(Bucket=self.bucket, Key=obj["Key"])["Body"].read()
            except Exception as exc:  # noqa: BLE001
                raise S3Error(f"could not read {obj['Key']}: {exc}") from exc
            path = (dest / relative).resolve()
            # Writing to dest_root itself is never legitimate (a key like
            # "knowledge/contributed/." resolves relative == "." straight to
            # dest_root), so it is rejected here rather than exempted by a
            # `path != dest_root` short-circuit.
            if path == dest_root or dest_root not in path.parents:
                raise S3Error(
                    f"refusing to write {obj['Key']!r} outside the cache directory"
                )
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(body)
            except OSError as exc:
                raise S3Error(f"could not write {obj['Key']} to the cache: {exc}") from exc
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
        if not self._exists(key):
            return False
        try:
            self.c.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:  # noqa: BLE001
            raise S3Error(f"could not delete {key}: {exc}") from exc
        return True
