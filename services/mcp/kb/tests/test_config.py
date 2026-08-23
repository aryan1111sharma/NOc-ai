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
