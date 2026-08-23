"""Write operations on the contributed corpus.

Separated from server.py so the rules — who may write, what is accepted, what
happens on failure — are testable without FastMCP, and so server.py stays
thin wiring.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

from .identity import Caller, is_contributor
from .ingest import ingest_file
from .parse import SUPPORTED, DocumentUnreadable
from .s3 import S3Corpus

log = logging.getLogger(__name__)

ACCEPTED = ", ".join(sorted(SUPPORTED))


class PermissionDenied(PermissionError):
    """Caller lacks a contributor role."""


class UploadRejected(ValueError):
    """The upload cannot be indexed, with a reason the user can act on."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KbWriter:
    def __init__(self, *, corpus: S3Corpus, registry, embedder, scope: str,
                 cache_dir, contributor_roles: tuple[str, ...], max_upload_mb: int,
                 max_chars: int, overlap: int, now=_utc_now):
        self.corpus = corpus
        self.registry = registry
        self.embedder = embedder
        self.scope = scope
        self.cache_dir = Path(cache_dir)
        self.roles = contributor_roles
        self.max_bytes = max_upload_mb * 1024 * 1024
        self.max_upload_mb = max_upload_mb
        self.max_chars = max_chars
        self.overlap = overlap
        self.now = now

    # -- guards ---------------------------------------------------------

    def _require_contributor(self, caller: Caller, action: str) -> None:
        if not is_contributor(caller, self.roles):
            log.warning("denied %s for user=%s role=%r", action, caller.user_id, caller.role)
            raise PermissionDenied(
                f"You do not have permission to {action} the knowledge base. This "
                f"requires one of these roles: {', '.join(self.roles) or '(none configured)'}. "
                "Ask an administrator to grant it in the LibreChat admin panel."
            )

    def _check_extension(self, filename: str) -> None:
        ext = Path(filename).suffix.lower()
        if ext == ".doc":
            raise UploadRejected(
                "Legacy .doc files cannot be read. Open it in Word and re-save it as .docx, "
                "then upload again."
            )
        if ext not in SUPPORTED:
            raise UploadRejected(
                f"{filename!r} is not a supported document type. Accepted: {ACCEPTED}."
            )

    # -- operations -----------------------------------------------------

    def upload(self, caller: Caller, filename: str) -> dict:
        self._require_contributor(caller, "add documents to")
        self._check_extension(filename)

        ref = self.corpus.find_user_upload(caller.user_id, filename)
        if ref is None:
            # Deliberately identical whether the file is absent or belongs to
            # someone else: this must not become an existence oracle.
            raise UploadRejected(
                # Deliberately does NOT echo the requested filename back: the
                # message must read identically whether the name is genuinely
                # absent or belongs to another user (see test_absent_and_
                # forbidden_files_give_the_same_message), otherwise the
                # filename in the error becomes an existence oracle.
                "No upload named that way was found among your recent uploads. "
                "Attach the file to this chat first, then ask again."
            )
        if ref.size > self.max_bytes:
            raise UploadRejected(
                f"{filename!r} is {ref.size / 1024 / 1024:.1f} MB, over the "
                f"{self.max_upload_mb} MB limit."
            )

        # cache_dir/<user_id>/<filename>, not cache_dir/<filename>: two
        # DIFFERENT callers uploading same-named files concurrently must not
        # be able to write over each other's local download between this
        # download and the hash/ingest read that follows it (see
        # test_cache_dir_is_not_shared_between_users_uploading_same_filename,
        # which reproduces the resulting content confusion). The SAME user
        # uploading the same filename twice concurrently still shares this
        # one path -- harmless, because both requests resolve to the same S3
        # object and the same corpus/source key regardless.
        #
        # caller.user_id is used here, unvalidated, as a path segment --
        # unlike corpus_key() below, which validates a filename before it
        # becomes an S3 key or a local path. Not reachable under this
        # server's trust model (user_id comes from the X-User-Id header
        # LibreChat injects server-side, never from the model -- see
        # identity.py), but corpus_key sets a precedent worth matching if
        # that ever changes.
        local = self.corpus.download_one(ref.key, self.cache_dir / caller.user_id / ref.filename)
        try:
            # ref.size came from the listing, before this download. An object
            # can grow between the two calls, so the cap is re-checked against
            # what was actually received -- otherwise the earlier check is a
            # TOCTOU gap a caller could race to bypass the limit entirely.
            actual_size = local.stat().st_size
            if actual_size > self.max_bytes:
                raise UploadRejected(
                    f"{filename!r} is {actual_size / 1024 / 1024:.1f} MB, over the "
                    f"{self.max_upload_mb} MB limit."
                )
            doc_hash = hashlib.sha256(local.read_bytes()).hexdigest()
            source = f"{self.scope}/{ref.filename}"
            store = self.registry.for_scope(self.scope)

            # A hash match in the index is NOT proof the corpus still holds
            # the file: the object can be deleted out of band (directly in
            # S3) while the chunks it produced stay indexed. Short-circuiting
            # on the hash alone would report "already indexed" for a
            # document that is about to vanish from the corpus for good — a
            # later `ingest --prune` walk mirrors S3 and removes chunks for
            # anything not on disk there, so the very next prune would
            # silently delete what this call just told the caller was fine.
            # Both conditions must hold to skip real work.
            hash_matches = store.current_doc_hash(source) == doc_hash
            repairing = hash_matches and not self.corpus.exists_in_corpus(ref.filename)
            if hash_matches and not repairing:
                existing = [d for d in store.list_documents(scopes=[self.scope])
                            if d["source"] == source]
                return {"source": source, "scope": self.scope, "already_indexed": True,
                        "replaced": False,
                        "chunks": existing[0]["chunks"] if existing else 0,
                        "message": f"{ref.filename} is already in the knowledge base, unchanged."}

            # Index BEFORE touching the corpus. The corpus is a flat, global
            # namespace shared by every contributor (see copy_to_corpus): a
            # filename collision is normal, not an edge case. Copying first
            # (the previous design) meant a rejection below had already
            # overwritten -- or, on cleanup, deleted outright -- a DIFFERENT
            # contributor's real document that merely shared this filename.
            # Indexing first means a rejection here never touches the corpus
            # at all, so it can only ever cost the uploader their own attempt.
            try:
                chunks = ingest_file(
                    local, source=source, scope_name=self.scope, store=store,
                    embedder=self.embedder, max_chars=self.max_chars, overlap=self.overlap,
                    doc_hash=doc_hash,
                    extra_payload={"uploaded_by": caller.email or caller.user_id,
                                   "uploaded_at": self.now()},
                )
            except DocumentUnreadable as exc:
                # Nothing has touched the corpus yet, so there is nothing to
                # clean up here -- just report the reason.
                raise UploadRejected(str(exc)) from exc

            if chunks == 0:
                # Same as above: ingest_file itself never touched the store
                # when it produces no chunks, and neither did we touch the
                # corpus, so there is nothing to roll back.
                raise UploadRejected(
                    f"{ref.filename!r} produced no readable text — it may be a scanned PDF or "
                    "an empty file. Scanned documents need OCR before they can be indexed."
                )

            # Only now, with the document proven good and indexed, does this
            # touch the corpus — so a failure here is the only case in this
            # method that still needs a rollback, and it only ever unwinds
            # work this call itself just did.
            try:
                _, replaced = self.corpus.copy_to_corpus(ref)
            except Exception:
                try:
                    store.delete_source(source)
                except Exception:
                    # Never let a rollback failure replace the real error (the
                    # failed copy) with a different one -- log it and let the
                    # original propagate regardless of what happens here.
                    log.exception(
                        "failed to roll back index for %s after a failed corpus copy", source
                    )
                raise
        finally:
            # Unconditional, on every path (success, rejection, or a raised
            # exception): cache_dir is nested under the directory the corpus
            # walker scans, so a downloaded file left behind here would later
            # be picked up and indexed as if it were a real, curated document.
            local.unlink(missing_ok=True)

        log.info("indexed %s (%d chunks) for user=%s repaired=%s",
                 source, chunks, caller.user_id, repairing)
        if repairing:
            # already_indexed is False here on purpose: this call did real
            # work (a fresh S3 copy and a full re-index), and the caller
            # should know that, not be told nothing happened.
            message = (f"{ref.filename} was already indexed, but its stored copy had "
                       "gone missing from the knowledge base — both have now been restored.")
        else:
            message = f"{ref.filename} is now searchable ({chunks} chunks)."
        return {"source": source, "scope": self.scope, "chunks": chunks,
                "already_indexed": False, "replaced": replaced,
                "message": message}

    def remove(self, caller: Caller, source: str) -> dict:
        self._require_contributor(caller, "remove documents from")
        prefix = f"{self.scope}/"
        if not source.startswith(prefix):
            raise UploadRejected(
                f"Only documents in the '{self.scope}' scope can be removed here; "
                f"{source!r} is curated material. Curated documents are managed in git."
            )
        filename = source[len(prefix):]
        # Validate before any S3 call: corpus_key() would reject a path-like
        # or otherwise unusable filename too, but its S3Error names the
        # internal corpus prefix (CORPUS_PREFIX) -- an implementation detail
        # that must not leak to a model-supplied `source`, and the wrong
        # exception type for a caller-facing tool besides. Mirrors
        # corpus_key's own rules without depending on its wording.
        if not filename or filename in (".", "..") or "/" in filename or "\\" in filename \
                or any(ch < " " for ch in filename):
            raise UploadRejected(
                f"{source!r} is not a valid document name in the '{self.scope}' scope."
            )
        store = self.registry.for_scope(self.scope)
        indexed = any(d["source"] == source for d in store.list_documents(scopes=[self.scope]))
        deleted = self.corpus.delete_from_corpus(filename)
        if indexed:
            store.delete_source(source)
        log.info("removed %s (object=%s, indexed=%s) for user=%s",
                 source, deleted, indexed, caller.user_id)
        return {"source": source, "removed": bool(indexed or deleted)}

    def list_documents(self, caller: Caller) -> list[dict]:
        """Read-only: any authenticated caller may see what is loaded."""
        return self.registry.for_scope(self.scope).list_documents(scopes=[self.scope])
