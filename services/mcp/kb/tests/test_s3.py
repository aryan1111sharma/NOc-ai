import pytest

from kb_mcp.s3 import CHAT_PREFIX, CORPUS_PREFIX, S3Corpus, S3Error, UploadRef
from tests.fakes import FakeS3


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
    key = corpus.copy_to_corpus(ref)
    assert key == f"{CORPUS_PREFIX}/runbook.docx"
    assert s3.objects[key] == b"data"


def test_download_corpus_writes_files_to_a_directory(tmp_path):
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/a.md", b"alpha")
    s3.put(f"{CORPUS_PREFIX}/b.md", b"beta")
    written = corpus.download_corpus(tmp_path)
    assert sorted(p.name for p in written) == ["a.md", "b.md"]
    assert (tmp_path / "a.md").read_bytes() == b"alpha"


def test_delete_from_corpus_removes_the_object():
    corpus, s3 = _corpus()
    s3.put(f"{CORPUS_PREFIX}/gone.md", b"x")
    assert corpus.delete_from_corpus("gone.md") is True
    assert f"{CORPUS_PREFIX}/gone.md" not in s3.objects


def test_delete_reports_false_for_an_absent_object():
    corpus, _ = _corpus()
    assert corpus.delete_from_corpus("never-existed.md") is False


def test_corpus_filenames_are_flattened_to_a_basename():
    # A caller must not be able to write outside the corpus prefix by passing
    # a path. This is the only place a caller-supplied name becomes a key.
    corpus, _ = _corpus()
    assert corpus.corpus_key("../../evil.md") == f"{CORPUS_PREFIX}/evil.md"


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
