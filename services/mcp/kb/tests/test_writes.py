import pytest
from qdrant_client import QdrantClient

from kb_mcp.identity import Caller
from kb_mcp.s3 import CHAT_PREFIX, CORPUS_PREFIX, S3Corpus
from kb_mcp.scopes import parse_scopes
from kb_mcp.store import StoreRegistry
from kb_mcp.writes import KbWriter, PermissionDenied, UploadRejected
from tests.fakes import FakeEmbedder, FakeS3

CFG = parse_scopes(
    {"scopes": {"ev-core": {}, "contributed": {}},
     "profiles": {"noc": {"scopes": ["ev-core", "contributed"], "description": "d"}}},
    source="test",
)
CONTRIBUTOR = Caller("u1", "noc@quench.local", "kb-contributor")
OUTSIDER = Caller("u2", "guest@quench.local", "noc-engineer")


def _writer(tmp_path, max_mb=25):
    s3 = FakeS3()
    emb = FakeEmbedder()
    reg = StoreRegistry(QdrantClient(":memory:"), CFG, shared_collection="ev_kb", dim=emb.dim)
    reg.ensure_all()
    writer = KbWriter(
        corpus=S3Corpus(s3, "test-bucket"), registry=reg, embedder=emb,
        scope="contributed", cache_dir=tmp_path,
        contributor_roles=("kb-contributor", "ADMIN"),
        max_upload_mb=max_mb, max_chars=1800, overlap=200,
        now=lambda: "2026-08-23T10:00:00Z",
    )
    return writer, s3, reg, emb


def test_upload_indexes_the_document_and_returns_a_summary(tmp_path):
    writer, s3, reg, emb = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md",
           b"Charger QNCH-1 reboots when the gun is unplugged mid-session.")

    result = writer.upload(CONTRIBUTOR, "note.md")

    assert result["source"] == "contributed/note.md"
    assert result["chunks"] == 1
    assert result["scope"] == "contributed"
    assert result["replaced"] is False
    assert s3.objects[f"{CORPUS_PREFIX}/note.md"]

    hits = reg.for_scope("contributed").search(emb.embed(["gun unplugged"])[0], top_k=3)
    assert hits[0]["source"] == "contributed/note.md"


def test_upload_stamps_uploader_and_timestamp(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"body text here")
    writer.upload(CONTRIBUTOR, "note.md")

    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert docs[0]["uploaded_by"] == "noc@quench.local"
    assert docs[0]["uploaded_at"] == "2026-08-23T10:00:00Z"


def test_upload_reports_when_it_replaced_someone_elses_document(tmp_path):
    """The corpus is flat and global while uploads are per-user, so two
    contributors who both attach notes.md collapse onto one key."""
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CORPUS_PREFIX}/notes.md", b"an earlier contributor's version")
    s3.put(f"{CHAT_PREFIX}/u1/abc__notes.md", b"a newer version of the notes")
    result = writer.upload(CONTRIBUTOR, "notes.md")
    assert result["replaced"] is True


def test_non_contributor_is_refused(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__note.md", b"body")
    with pytest.raises(PermissionDenied, match="kb-contributor"):
        writer.upload(OUTSIDER, "note.md")


def test_refusal_happens_before_any_s3_call(tmp_path):
    """Not just 'before any write' — a non-contributor must not be able to
    drive an S3 GET or a local disk write either."""
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__note.md", b"body")

    calls = []
    for name in ("list_objects_v2", "get_object", "copy_object", "delete_object", "head_object"):
        original = getattr(s3, name)

        def recorded(*a, _n=name, _o=original, **k):
            calls.append(_n)
            return _o(*a, **k)

        setattr(s3, name, recorded)

    with pytest.raises(PermissionDenied):
        writer.upload(OUTSIDER, "note.md")
    assert calls == [], f"a refused caller reached S3: {calls}"
    assert not any(tmp_path.rglob("*")), "a refused caller wrote to the cache directory"


def test_another_users_file_is_not_reachable(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u2/abc__private.md", b"someone else's")
    with pytest.raises(UploadRejected, match="No upload named"):
        writer.upload(CONTRIBUTOR, "private.md")


def test_absent_and_forbidden_files_give_the_same_message(tmp_path):
    """The tool must not become an existence oracle: asking for a file that
    belongs to someone else must be indistinguishable from asking for one
    that exists nowhere. Same filename in both cases -- only the other user's
    possession of it differs."""
    forbidden_writer, s3_with, _, _ = _writer(tmp_path / "a")
    s3_with.put(f"{CHAT_PREFIX}/u2/abc__report.md", b"another user's document")
    absent_writer, _, _, _ = _writer(tmp_path / "b")

    with pytest.raises(UploadRejected) as forbidden:
        forbidden_writer.upload(CONTRIBUTOR, "report.md")
    with pytest.raises(UploadRejected) as absent:
        absent_writer.upload(CONTRIBUTOR, "report.md")

    assert str(forbidden.value) == str(absent.value)


def test_the_not_found_message_never_contains_the_requested_filename(tmp_path):
    """Guards against reintroducing an existence oracle: the previous test
    uses the same filename in both branches, so it cannot by itself catch a
    message that echoes a filename back (that would still match in both
    branches and the equality assertion would still pass). This test checks
    the stronger property directly: the message contains no filename at
    all, for any requested name."""
    writer, s3, _, _ = _writer(tmp_path)
    with pytest.raises(UploadRejected) as exc:
        writer.upload(CONTRIBUTOR, "a-very-distinctive-name-xyz123.md")
    assert "a-very-distinctive-name-xyz123" not in str(exc.value)


def test_unsupported_extension_is_refused_with_the_accepted_list(tmp_path):
    # .pptx was the example here until the parser gained it; an extension used to
    # stand for "unsupported" has to be one the indexer genuinely cannot read, or
    # the test starts passing for the wrong reason as SUPPORTED grows.
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__archive.zip", b"x")
    with pytest.raises(UploadRejected, match=r"\.docx"):
        writer.upload(CONTRIBUTOR, "archive.zip")


def test_legacy_doc_gets_a_specific_message(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__old.doc", b"x")
    with pytest.raises(UploadRejected, match="re-save it as .docx"):
        writer.upload(CONTRIBUTOR, "old.doc")


def test_oversize_upload_is_refused_before_download(tmp_path):
    writer, s3, _, _ = _writer(tmp_path, max_mb=1)
    s3.put(f"{CHAT_PREFIX}/u1/abc__big.md", b"x" * (2 * 1024 * 1024))
    with pytest.raises(UploadRejected, match="1 MB"):
        writer.upload(CONTRIBUTOR, "big.md")
    assert f"{CORPUS_PREFIX}/big.md" not in s3.objects


def test_unextractable_document_fails_loudly_and_cleans_up(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__blank.txt", b"   \n\n   ")
    with pytest.raises(UploadRejected, match="no readable text"):
        writer.upload(CONTRIBUTOR, "blank.txt")
    assert f"{CORPUS_PREFIX}/blank.txt" not in s3.objects, \
        "a failed upload must not leave an object behind for the corpus walk"


def test_a_corrupt_document_is_reported_in_plain_language(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__fake.docx", b"this is not really a Word file")
    with pytest.raises(UploadRejected, match="may be corrupt"):
        writer.upload(CONTRIBUTOR, "fake.docx")
    assert f"{CORPUS_PREFIX}/fake.docx" not in s3.objects


def test_identical_bytes_are_reported_as_already_indexed(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"stable content for hashing")
    first = writer.upload(CONTRIBUTOR, "note.md")
    second = writer.upload(CONTRIBUTOR, "note.md")

    assert first["chunks"] == 1
    assert second["already_indexed"] is True
    assert second["chunks"] == 1
    assert len(reg.for_scope("contributed").list_documents(scopes=["contributed"])) == 1


def test_changed_bytes_replace_the_previous_version(tmp_path):
    writer, s3, reg, emb = _writer(tmp_path)
    key = f"{CHAT_PREFIX}/u1/abc__note.md"
    s3.put(key, b"first version, about connector wear")
    writer.upload(CONTRIBUTOR, "note.md")

    s3.put(key, b"second version, about firmware rollback procedure")
    result = writer.upload(CONTRIBUTOR, "note.md")

    assert result["already_indexed"] is False
    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert len(docs) == 1
    hits = reg.for_scope("contributed").search(emb.embed(["firmware rollback"])[0], top_k=3)
    assert "firmware rollback" in hits[0]["text"]


def test_remove_deletes_the_object_and_its_chunks(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content to be removed later")
    writer.upload(CONTRIBUTOR, "note.md")

    result = writer.remove(CONTRIBUTOR, "contributed/note.md")

    assert result["removed"] is True
    assert f"{CORPUS_PREFIX}/note.md" not in s3.objects
    assert reg.for_scope("contributed").list_documents(scopes=["contributed"]) == []


def test_remove_is_refused_for_non_contributors(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content")
    writer.upload(CONTRIBUTOR, "note.md")
    with pytest.raises(PermissionDenied):
        writer.remove(OUTSIDER, "contributed/note.md")


def test_remove_refuses_to_touch_the_curated_scope(tmp_path):
    writer, _, _, _ = _writer(tmp_path)
    with pytest.raises(UploadRejected, match="contributed"):
        writer.remove(CONTRIBUTOR, "ev-core/ocpp.md")


def test_remove_reports_a_missing_document(tmp_path):
    writer, _, _, _ = _writer(tmp_path)
    result = writer.remove(CONTRIBUTOR, "contributed/never-existed.md")
    assert result["removed"] is False


def test_list_documents_needs_no_special_role(tmp_path):
    writer, s3, _, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"listed content")
    writer.upload(CONTRIBUTOR, "note.md")
    docs = writer.list_documents(OUTSIDER)
    assert [d["source"] for d in docs] == ["contributed/note.md"]


# --- Adversarial regression tests (Task 11 self-review) ---------------------

def test_cache_dir_is_not_shared_between_users_uploading_same_filename(tmp_path):
    """Two different users uploading a same-named file must not have one
    caller's local download clobber another's mid-operation. Simulates the
    interleaving a concurrent server would produce: while user one's download
    is in flight (specifically, in the window between download_one's write
    and the writer's subsequent read_bytes()), a second, unrelated request
    for a different user uploading the same filename runs to completion.

    copy_to_corpus always copies user one's real bytes into the corpus
    server-side, straight from S3 -- it never goes through the local cache.
    So regardless of ordering, the invariant that must hold is: whatever text
    ends up indexed for a source must match what is actually stored in the
    corpus object for it. A shared local cache path can violate that by
    making user one hash and chunk user two's bytes instead of their own.
    """
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"user one's real content")
    s3.put(f"{CHAT_PREFIX}/u2/xyz__note.md", b"user two's unrelated content")
    contributor2 = Caller("u2", "other@quench.local", "kb-contributor")

    real_download_one = writer.corpus.download_one
    triggered = {"done": False}

    def racy_download_one(key, dest):
        path = real_download_one(key, dest)
        if not triggered["done"] and key.startswith(f"{CHAT_PREFIX}/u1/"):
            triggered["done"] = True
            # A concurrent request for a different user, uploading a
            # same-named file, runs to completion before this caller (user
            # one) reads its own downloaded file back.
            writer.upload(contributor2, "note.md")
        return path

    writer.corpus.download_one = racy_download_one
    writer.upload(CONTRIBUTOR, "note.md")

    # u1's own request is the one that chronologically finishes last (the
    # nested u2 request runs to completion first, inside u1's download), so
    # under correct "last writer wins" semantics for the shared corpus slot,
    # the final corpus object -- and what is indexed for it -- must be u1's
    # own content, not u2's.
    corpus_bytes = s3.objects[f"{CORPUS_PREFIX}/note.md"]
    assert corpus_bytes == b"user one's real content", (
        f"user one's own upload did not win the corpus slot it should have: "
        f"corpus holds {corpus_bytes!r}"
    )
    hits = reg.for_scope("contributed").search(
        writer.embedder.embed(["real content"])[0], top_k=3)
    assert hits[0]["text"].encode() == corpus_bytes, (
        f"indexed text does not match the corpus object: "
        f"indexed={hits[0]['text']!r} corpus={corpus_bytes!r}"
    )


def test_embedder_outage_after_copy_does_not_orphan_the_corpus_object(tmp_path):
    """If ingest_file fails for a reason OTHER than DocumentUnreadable (an
    embedder outage, a Qdrant failure) after the object has already been
    copied into the corpus, the copy must not be left behind -- otherwise a
    later corpus walk silently picks it up with no provenance (no
    uploaded_by/uploaded_at). The underlying failure itself must still
    propagate rather than being swallowed."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content that will fail to embed")

    class ExplodingEmbedder:
        dim = 8

        def embed(self, texts):
            raise RuntimeError("embedder is down")

    writer.embedder = ExplodingEmbedder()

    with pytest.raises(RuntimeError, match="embedder is down"):
        writer.upload(CONTRIBUTOR, "note.md")

    assert f"{CORPUS_PREFIX}/note.md" not in s3.objects, (
        "a failure after copy but before indexing must not leave an orphaned "
        "object behind for a later corpus walk to trip over"
    )


def test_size_check_uses_the_listing_not_the_downloaded_bytes(tmp_path):
    """The size cap is read from ref.size (the S3 listing) before download.
    If the object grows between the list call and the download call, the
    cap must still hold against what is actually downloaded -- otherwise a
    caller can bypass the limit by racing the check."""
    writer, s3, reg, _ = _writer(tmp_path, max_mb=1)
    key = f"{CHAT_PREFIX}/u1/abc__big.md"
    s3.put(key, b"small enough to pass the size check")

    real_get_object = s3.get_object
    grown = {"done": False}

    def growing_get_object(Bucket, Key):
        if Key == key and not grown["done"]:
            grown["done"] = True
            # The object grows between the listing (find_user_upload, which
            # already ran and reported a small size) and this download.
            s3.put(key, b"x" * (2 * 1024 * 1024))
        return real_get_object(Bucket, Key)

    s3.get_object = growing_get_object

    with pytest.raises(UploadRejected, match="1 MB"):
        writer.upload(CONTRIBUTOR, "big.md")
    assert f"{CORPUS_PREFIX}/big.md" not in s3.objects


def test_remove_reports_true_when_object_already_gone_but_chunks_remain(tmp_path):
    """Simulates the corpus object having been deleted out-of-band (e.g. by
    someone editing S3 directly) while the chunks remain indexed. `removed`
    must still be true and the chunks must still come out."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content")
    writer.upload(CONTRIBUTOR, "note.md")
    del s3.objects[f"{CORPUS_PREFIX}/note.md"]
    s3.times.pop(f"{CORPUS_PREFIX}/note.md", None)

    result = writer.remove(CONTRIBUTOR, "contributed/note.md")
    assert result["removed"] is True
    assert reg.for_scope("contributed").list_documents(scopes=["contributed"]) == []


def test_remove_reports_true_when_chunks_already_gone_but_object_remains(tmp_path):
    """The reverse: an object sitting in the corpus with nothing indexed for
    it (e.g. an out-of-band S3 write, or an ingest that failed to reach the
    store). `removed` must still be true and the object must still go."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CORPUS_PREFIX}/orphan.md", b"never indexed")

    result = writer.remove(CONTRIBUTOR, "contributed/orphan.md")
    assert result["removed"] is True
    assert f"{CORPUS_PREFIX}/orphan.md" not in s3.objects


def test_a_missing_corpus_object_is_repaired_rather_than_reported_as_present(tmp_path):
    """A hash match in the index is not proof the corpus still holds the file.
    Reporting "already indexed" for a document whose object has gone means the
    next prune silently removes it, after the user was told it was fine."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"stable content for hashing")
    writer.upload(CONTRIBUTOR, "note.md")
    assert f"{CORPUS_PREFIX}/note.md" in s3.objects

    # The object disappears out of band; the index still has its chunks.
    del s3.objects[f"{CORPUS_PREFIX}/note.md"]

    result = writer.upload(CONTRIBUTOR, "note.md")
    assert f"{CORPUS_PREFIX}/note.md" in s3.objects, "the corpus object must be restored"
    assert result["already_indexed"] is False


def test_an_unchanged_document_whose_object_is_present_is_still_a_no_op(tmp_path):
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"stable content for hashing")
    writer.upload(CONTRIBUTOR, "note.md")
    result = writer.upload(CONTRIBUTOR, "note.md")
    assert result["already_indexed"] is True


# --- Security review follow-up: index-before-copy reorder -------------------

def test_a_blank_upload_does_not_destroy_the_document_it_would_have_replaced(tmp_path):
    """Reviewer's reproduction: the corpus is a flat, global namespace, so a
    SECOND, unrelated contributor's blank/unreadable upload sharing the same
    filename must not destroy the FIRST contributor's real document. Before
    the fix, copy_to_corpus ran before the chunks==0 check, so engineer two's
    (empty) bytes overwrote engineer one's corpus object, and the subsequent
    cleanup deleted it outright -- leaving engineer one's chunks in the index
    with no corresponding corpus object."""
    writer, s3, reg, _ = _writer(tmp_path)
    engineer2 = Caller("u2", "two@quench.local", "kb-contributor")

    s3.put(f"{CHAT_PREFIX}/u1/abc__runbook.txt", b"real maintenance procedure")
    writer.upload(CONTRIBUTOR, "runbook.txt")
    assert s3.objects[f"{CORPUS_PREFIX}/runbook.txt"] == b"real maintenance procedure"

    s3.put(f"{CHAT_PREFIX}/u2/xyz__runbook.txt", b"   \n\n  ")
    with pytest.raises(UploadRejected, match="no readable text"):
        writer.upload(engineer2, "runbook.txt")

    assert s3.objects[f"{CORPUS_PREFIX}/runbook.txt"] == b"real maintenance procedure", (
        "engineer one's corpus object must survive engineer two's rejected upload"
    )
    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert len(docs) == 1
    assert docs[0]["source"] == "contributed/runbook.txt"
    assert docs[0]["uploaded_by"] == "noc@quench.local"


def test_a_transient_infra_failure_during_a_colliding_reupload_does_not_destroy_the_existing_document(tmp_path):
    """Reviewer's reproduction, generalized to a transient infrastructure
    failure rather than bad content: a second contributor's upload, sharing
    the first's filename, fails partway through indexing (simulated here as
    an embedding-service outage, which -- per ingest_file's own design --
    happens before the store is touched at all). The first contributor's
    corpus object and index entry must both survive untouched.

    A failure landing inside ingest_file's own narrow window between its
    internal delete_source() and upsert() calls is a separate, already-
    documented residual risk of ingest.py itself (see its docstring: "Qdrant
    has no transaction to borrow") and is not owned by this task -- this
    test does not exercise that narrower window.
    """
    writer, s3, reg, emb = _writer(tmp_path)
    engineer2 = Caller("u2", "two@quench.local", "kb-contributor")

    s3.put(f"{CHAT_PREFIX}/u1/abc__runbook.txt", b"energise the charger during service")
    writer.upload(CONTRIBUTOR, "runbook.txt")

    class FlakyEmbedder:
        dim = emb.dim

        def embed(self, texts):
            raise RuntimeError("embedding service unavailable")

    writer.embedder = FlakyEmbedder()
    s3.put(f"{CHAT_PREFIX}/u2/xyz__runbook.txt", b"isolate the charger before service")
    with pytest.raises(RuntimeError, match="embedding service unavailable"):
        writer.upload(engineer2, "runbook.txt")
    writer.embedder = emb

    assert s3.objects[f"{CORPUS_PREFIX}/runbook.txt"] == b"energise the charger during service"
    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert len(docs) == 1
    assert docs[0]["source"] == "contributed/runbook.txt"


def test_a_failed_corpus_copy_rolls_back_the_index_without_masking_the_original_error(tmp_path):
    """The only remaining cleanup site after the reorder: if copy_to_corpus
    itself fails (S3 unhealthy) AFTER indexing already succeeded, the index
    is rolled back so it doesn't reference a document that isn't actually in
    the corpus. If that rollback ALSO fails, the original copy failure must
    still be what the caller sees -- never replaced by the rollback's own
    exception."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content for a copy that will fail")

    def failing_copy(ref):
        raise RuntimeError("S3 is unavailable for copy_object")

    writer.corpus.copy_to_corpus = failing_copy

    with pytest.raises(RuntimeError, match="S3 is unavailable for copy_object"):
        writer.upload(CONTRIBUTOR, "note.md")

    # The index must not reference a document that was never actually
    # copied into the corpus.
    docs = reg.for_scope("contributed").list_documents(scopes=["contributed"])
    assert docs == []


def test_a_failed_corpus_copy_rollback_failure_still_surfaces_the_original_error(tmp_path):
    """ingest_file itself calls store.delete_source(source) once, normally,
    before the rollback is ever in play (to clear any previous version
    before upserting the new one). The failure under test must specifically
    be the ROLLBACK's own call to delete_source, not that ordinary one --
    otherwise this test would validate the wrong code path entirely."""
    writer, s3, reg, _ = _writer(tmp_path)
    s3.put(f"{CHAT_PREFIX}/u1/abc__note.md", b"content for a copy that will fail")

    def failing_copy(ref):
        raise RuntimeError("S3 is unavailable for copy_object")

    writer.corpus.copy_to_corpus = failing_copy
    store = writer.registry.for_scope("contributed")
    real_delete_source = store.delete_source
    calls = {"n": 0}

    def flaky_delete_source(source):
        calls["n"] += 1
        if calls["n"] == 1:
            return real_delete_source(source)
        raise RuntimeError("Qdrant is also unavailable during rollback")

    store.delete_source = flaky_delete_source

    with pytest.raises(RuntimeError, match="S3 is unavailable for copy_object"):
        writer.upload(CONTRIBUTOR, "note.md")

    store.delete_source = real_delete_source


# --- Security review follow-up: remove() must not leak internal paths -------

def test_remove_rejects_a_path_like_document_name_without_leaking_internals(tmp_path):
    """A model can pass any `source`; a path-like one must not surface a raw
    S3Error exposing the internal corpus prefix, and the reason given must be
    the actionable UploadRejected message the rest of this module uses."""
    writer, s3, _, _ = _writer(tmp_path)
    with pytest.raises(UploadRejected) as exc:
        writer.remove(CONTRIBUTOR, "contributed/../../evil.md")
    assert "knowledge/contributed" not in str(exc.value)


def test_local_cache_file_is_removed_on_every_path(tmp_path):
    """cache_dir is nested under the directory the corpus walker scans (per
    the comment in upload()), so any leftover download would later be
    indexed as if it were a real, curated document -- with the uploader's
    own user id baked into a bogus `source` path. Every path through
    upload(), success and rejection alike, must leave no file behind."""
    writer, s3, _, _ = _writer(tmp_path)

    s3.put(f"{CHAT_PREFIX}/u1/abc__ok.md", b"real content")
    writer.upload(CONTRIBUTOR, "ok.md")

    s3.put(f"{CHAT_PREFIX}/u1/abc__blank.txt", b"   ")
    with pytest.raises(UploadRejected):
        writer.upload(CONTRIBUTOR, "blank.txt")

    s3.put(f"{CHAT_PREFIX}/u1/abc__fake.docx", b"not a real docx")
    with pytest.raises(UploadRejected):
        writer.upload(CONTRIBUTOR, "fake.docx")

    leftover_files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert leftover_files == [], f"cache directory has leftover files: {leftover_files}"
