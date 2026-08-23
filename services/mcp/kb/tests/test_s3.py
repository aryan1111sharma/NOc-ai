from pathlib import Path

import pytest

from kb_mcp.s3 import CHAT_PREFIX, CORPUS_PREFIX, S3Corpus, S3Error, UploadRef
from tests.fakes import FakeClientError, FakeS3


def _corpus():
    s3 = FakeS3()
    return S3Corpus(s3, "test-bucket"), s3


def test_finds_a_users_upload_by_original_filename():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc-123__runbook.docx", b"data")
    ref = corpus.find_user_upload("u1", "runbook.docx")
    assert isinstance(ref, UploadRef)
    assert ref.key == f"{CHAT_PREFIX}/u1/abc-123__runbook.docx"
    assert ref.filename == "runbook.docx" and ref.size == 4


def test_filename_match_is_case_insensitive():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__Runbook.DOCX", b"data")
    assert corpus.find_user_upload("u1", "runbook.docx") is not None


def test_returns_the_newest_when_a_name_was_uploaded_twice():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/old__spec.pdf", b"old", age_minutes=60)
    s3.put(f"{CHAT_PREFIX}/u1/new__spec.pdf", b"new", age_minutes=1)
    assert corpus.find_user_upload("u1", "spec.pdf").key.endswith("new__spec.pdf")


def test_cannot_see_another_users_upload():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u2/abc__secret.docx", b"data")
    assert corpus.find_user_upload("u1", "secret.docx") is None


def test_a_user_whose_id_is_a_prefix_of_another_cannot_see_their_uploads():
    """Isolation must survive the trailing slash being dropped from the
    prefix — a one-character regression that otherwise passes every test."""
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1extra/abc__salary-review.docx", b"someone else's")
    assert corpus.find_user_upload("u1", "salary-review.docx") is None


def test_empty_filename_returns_none_instead_of_a_bogus_match():
    corpus, s3 = _corpus()
    # A folder-marker object (zero-byte, key ending in "/") would otherwise
    # be treated as a match for an empty/blank requested filename.
    s3.put(f"{CHAT_PREFIX}/u1/", b"")
    assert corpus.find_user_upload("u1", "") is None
    assert corpus.find_user_upload("u1", "   ") is None


def test_missing_upload_returns_none():
    corpus, _ = _corpus()
    assert corpus.find_user_upload("u1", "nope.pdf") is None


def test_a_file_with_no_uuid_prefix_still_matches():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/plain.txt", b"x")
    assert corpus.find_user_upload("u1", "plain.txt") is not None


def test_copy_to_corpus_places_the_object_under_the_corpus_prefix():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__runbook.docx", b"data")
    ref = corpus.find_user_upload("u1", "runbook.docx")
    key, replaced = corpus.copy_to_corpus(ref)
    assert key == f"{CORPUS_PREFIX}/runbook.docx"
    assert replaced is False
    assert s3.objects[key] == b"data"


def test_copy_to_corpus_reports_when_it_replaces_an_existing_document():
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/notes.md", b"first contributor's version")
    s3.put(f"{CHAT_PREFIX}/u2/abc__notes.md", b"second contributor's version")
    ref = corpus.find_user_upload("u2", "notes.md")
    key, replaced = corpus.copy_to_corpus(ref)
    assert replaced is True
    assert s3.objects[key] == b"second contributor's version"


def test_copy_to_corpus_reports_a_new_document_as_not_replacing():
    corpus, s3 = _corpus()
    s3.put(f"{CHAT_PREFIX}/u1/abc__fresh.md", b"new")
    ref = corpus.find_user_upload("u1", "fresh.md")
    _, replaced = corpus.copy_to_corpus(ref)
    assert replaced is False


def test_download_corpus_writes_files_to_a_directory(tmp_path):
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/a.md", b"alpha")
    s3.put(f"{CORPUS_PREFIX}/b.md", b"beta")
    written = corpus.download_corpus(tmp_path)
    assert sorted(p.name for p in written) == ["a.md", "b.md"]
    assert (tmp_path / "a.md").read_bytes() == b"alpha"


def test_download_corpus_preserves_nested_keys_with_colliding_basenames(tmp_path):
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/sub1/dup.md", b"one")
    s3.put(f"{CORPUS_PREFIX}/sub2/dup.md", b"two")
    written = corpus.download_corpus(tmp_path)
    assert len(written) == 2
    assert (tmp_path / "sub1" / "dup.md").read_bytes() == b"one"
    assert (tmp_path / "sub2" / "dup.md").read_bytes() == b"two"


def test_download_corpus_refuses_to_escape_the_destination_directory(tmp_path):
    corpus, s3 = _corpus()
    # An out-of-band actor could place a key with a path-escaping component
    # directly in S3 (e.g. via the console), bypassing corpus_key entirely.
    s3.put(f"{CORPUS_PREFIX}/../../evil.md", b"escaped")
    with pytest.raises(S3Error):
        corpus.download_corpus(tmp_path)


def test_download_corpus_rejects_a_key_that_resolves_to_the_destination_itself(tmp_path):
    # A key of "knowledge/contributed/." yields relative == "." and resolves
    # to dest_root exactly. Writing to the directory itself is never
    # legitimate and must be rejected, not silently exempted from the check.
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/.", b"weird")
    with pytest.raises(S3Error):
        corpus.download_corpus(tmp_path)


def test_download_corpus_wraps_a_local_write_failure_as_s3error(tmp_path, monkeypatch):
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/a.md", b"alpha")

    def boom(self, *args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(S3Error, match="could not write"):
        corpus.download_corpus(tmp_path)


def test_delete_from_corpus_removes_the_object():
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/gone.md", b"x")
    assert corpus.delete_from_corpus("gone.md") is True
    assert f"{CORPUS_PREFIX}/gone.md" not in s3.objects


def test_delete_reports_false_for_an_absent_object():
    corpus, _ = _corpus()
    assert corpus.delete_from_corpus("never-existed.md") is False


def test_exists_raises_rather_than_reporting_absent_when_s3_fails():
    """A 403 or a connection flap must not read as "not there" — that tells a
    caller their document was already gone when it exists."""
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/runbook.md", b"real content")

    def forbidden(**kwargs):
        raise FakeClientError("403", "AccessDenied: s3:GetObject")

    s3.head_object = forbidden
    with pytest.raises(S3Error, match="could not check whether"):
        corpus.delete_from_corpus("runbook.md")


def test_a_genuine_404_still_reports_absent():
    corpus, _ = _corpus()
    assert corpus.delete_from_corpus("never-existed.md") is False


def test_copy_raises_rather_than_misreporting_replacement_when_head_fails():
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/notes.md", b"existing")
    s3.put(f"{CHAT_PREFIX}/u1/abc__notes.md", b"new")
    ref = corpus.find_user_upload("u1", "notes.md")

    def flaky(**kwargs):
        raise ConnectionError("reset by peer")

    s3.head_object = flaky
    with pytest.raises(S3Error):
        corpus.copy_to_corpus(ref)


def test_corpus_filenames_containing_a_path_are_rejected():
    corpus, _ = _corpus()
    for bad in ("../../evil.md", "..\\..\\evil.md", "2026/report.md", "/etc/passwd"):
        with pytest.raises(S3Error):
            corpus.corpus_key(bad)


def test_a_plain_filename_is_accepted():
    corpus, _ = _corpus()
    assert corpus.corpus_key("runbook.docx") == f"{CORPUS_PREFIX}/runbook.docx"


def test_corpus_filenames_with_control_characters_are_rejected():
    # Control characters are legal in an S3 key but fatal on the filesystem
    # later (e.g. embedded NUL raises ValueError from the OS layer).
    corpus, _ = _corpus()
    for bad in ("evil\x00.md", "evil\n.md", "evil\r.md"):
        with pytest.raises(S3Error):
            corpus.corpus_key(bad)


def test_pagination_follows_continuation_token_and_accumulates(monkeypatch):
    corpus, s3 = _corpus()
    pages = [
        {"Contents": [{"Key": f"{CORPUS_PREFIX}/a.md", "Size": 1, "LastModified": None}],
         "IsTruncated": True, "NextContinuationToken": "tok2"},
        {"Contents": [{"Key": f"{CORPUS_PREFIX}/b.md", "Size": 1, "LastModified": None}],
         "IsTruncated": False},
    ]
    calls = []

    def fake_list(Bucket, Prefix, ContinuationToken=None):
        calls.append(ContinuationToken)
        return pages[len(calls) - 1]

    monkeypatch.setattr(s3, "list_objects_v2", fake_list)
    out = corpus._list(f"{CORPUS_PREFIX}/")
    assert [o["Key"] for o in out] == [f"{CORPUS_PREFIX}/a.md", f"{CORPUS_PREFIX}/b.md"]
    assert calls == [None, "tok2"]


def test_pagination_raises_instead_of_looping_forever_on_a_malformed_response(monkeypatch):
    # A server that claims IsTruncated=True but supplies no token would spin
    # this loop forever (same request repeated). Refuse loudly instead.
    corpus, s3 = _corpus()

    def fake_list(Bucket, Prefix, ContinuationToken=None):
        return {"Contents": [], "IsTruncated": True}

    monkeypatch.setattr(s3, "list_objects_v2", fake_list)
    with pytest.raises(S3Error):
        corpus._list(f"{CORPUS_PREFIX}/")


class _BrokenClient:
    """Every S3 call fails, uniformly. _exists now raises on any failure that
    isn't a recognised not-found (see kb_mcp.s3._exists), so head_object can
    fail here too — delete_from_corpus and copy_to_corpus reach S3Error via
    _exists itself rather than needing head_object to succeed first."""

    def list_objects_v2(self, **kwargs):
        raise RuntimeError("boom")

    def get_object(self, **kwargs):
        raise RuntimeError("boom")

    def copy_object(self, **kwargs):
        raise RuntimeError("boom")

    def delete_object(self, **kwargs):
        raise RuntimeError("boom")

    def head_object(self, **kwargs):
        raise RuntimeError("boom")


def test_every_public_method_wraps_client_failures_as_s3error(tmp_path):
    corpus = S3Corpus(_BrokenClient(), "test-bucket")
    ref = UploadRef(key=f"{CHAT_PREFIX}/u1/abc__x.md", filename="x.md", size=1)

    with pytest.raises(S3Error):
        corpus.find_user_upload("u1", "x.md")
    with pytest.raises(S3Error):
        corpus.copy_to_corpus(ref)
    with pytest.raises(S3Error):
        corpus.download_corpus(tmp_path)
    with pytest.raises(S3Error):
        corpus.download_one(f"{CORPUS_PREFIX}/x.md", tmp_path / "x.md")
    with pytest.raises(S3Error):
        corpus.delete_from_corpus("x.md")
