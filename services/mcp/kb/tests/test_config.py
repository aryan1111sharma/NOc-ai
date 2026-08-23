import pytest
from pydantic import ValidationError

from kb_mcp.config import Settings


def test_contributor_roles_parse_into_a_tuple():
    s = Settings(kb_contributor_roles="kb-contributor,ADMIN")
    assert s.contributor_roles == ("kb-contributor", "ADMIN")


def test_contributor_roles_tolerate_spaces_and_empties():
    s = Settings(kb_contributor_roles=" kb-contributor , , ADMIN ")
    assert s.contributor_roles == ("kb-contributor", "ADMIN")


def test_empty_contributor_roles_grants_nobody():
    assert Settings(kb_contributor_roles="").contributor_roles == ()


def test_upload_defaults():
    s = Settings()
    assert s.kb_max_upload_mb == 25
    assert s.contributed_scope == "contributed"
    assert s.contributed_dir == "/knowledge/contributed"
    assert s.aws_region == "ap-south-1"


def test_no_bucket_by_default_so_uploads_stay_disabled_until_configured():
    """A KB server with no bucket must still serve search; the upload tools
    simply do not get registered."""
    assert Settings().s3_bucket == ""


def test_a_zero_or_negative_upload_cap_is_refused_at_startup():
    """A nonsensical cap rejects every upload with a message that reads like
    the file is too big. Fail where the cause is visible instead."""
    for bad in (0, -5):
        with pytest.raises(ValidationError):
            Settings(kb_max_upload_mb=bad)


def test_a_positive_upload_cap_is_accepted():
    assert Settings(kb_max_upload_mb=1).kb_max_upload_mb == 1
    assert Settings(kb_max_upload_mb=100).kb_max_upload_mb == 100


def test_the_upload_cache_is_not_inside_the_walked_corpus_tree():
    """A cache under contributed_dir is walked by the corpus ingest pass, so
    every upload gets indexed a second time as `contributed/<user id>/<name>`
    — with the uploader's id in the source string the agent cites — and a
    rolled-back upload returns as a real document."""
    s = Settings()
    assert s.upload_cache_dir == "/var/cache/kb-uploads"
    assert not s.upload_cache_dir.startswith(s.contributed_dir)
    assert not s.upload_cache_dir.startswith("/knowledge")
