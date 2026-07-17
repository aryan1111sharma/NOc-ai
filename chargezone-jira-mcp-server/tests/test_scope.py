import pytest

from cz_jira_mcp.server import build_scoped_jql, require_qch


@pytest.mark.parametrize("key", ["QCH-1", "QCH-4490", "  QCH-7  "])
def test_require_qch_accepts_valid_keys(key):
    assert require_qch(key) == key.strip()


@pytest.mark.parametrize("key", ["CHAR-1", "ADOR-9", "qch-1", "QCH-", "QCHX-1", "QCH1", "", "  ", "QCH-１２"])
def test_require_qch_rejects_foreign_or_malformed(key):
    with pytest.raises(ValueError, match="QCH"):
        require_qch(key)


def test_build_scoped_jql_empty_uses_default_order():
    assert build_scoped_jql("") == 'project = "QCH" ORDER BY updated DESC'


def test_build_scoped_jql_wraps_conditions_and_adds_default_order():
    assert build_scoped_jql('status = "To Do"') == \
        'project = "QCH" AND (status = "To Do") ORDER BY updated DESC'


def test_build_scoped_jql_keeps_user_order_by_outside_parens():
    assert build_scoped_jql("status = Open ORDER BY created ASC") == \
        'project = "QCH" AND (status = Open) ORDER BY created ASC'


def test_build_scoped_jql_order_by_only():
    assert build_scoped_jql("ORDER BY created DESC") == 'project = "QCH" ORDER BY created DESC'


def test_build_scoped_jql_foreign_project_is_neutralized_by_and():
    # A smuggled project filter stays ANDed under QCH → resolves to empty, never widens scope.
    assert build_scoped_jql("project = ADNOC") == \
        'project = "QCH" AND (project = ADNOC) ORDER BY updated DESC'


@pytest.mark.parametrize("evil", [
    'status = Open) OR project != QCH OR (status = Open',
    'status = Open) OR project = ADNOC OR (status = Open',
    ')',
    'a) OR (b',
])
def test_build_scoped_jql_rejects_unbalanced_parens(evil):
    with pytest.raises(ValueError, match="parenthes"):
        build_scoped_jql(evil)


def test_build_scoped_jql_allows_balanced_and_quoted_parens():
    # legitimate OR inside the wrapper, and a literal ')' inside a quoted string
    assert build_scoped_jql('status = Open OR status = "In Progress"') == \
        'project = "QCH" AND (status = Open OR status = "In Progress") ORDER BY updated DESC'
    assert build_scoped_jql('summary ~ "foo)bar"') == \
        'project = "QCH" AND (summary ~ "foo)bar") ORDER BY updated DESC'


def test_build_scoped_jql_whitespace_only_is_default():
    assert build_scoped_jql("   ") == 'project = "QCH" ORDER BY updated DESC'


def test_build_scoped_jql_lowercase_order_by_kept_outside_parens():
    assert build_scoped_jql("status = Open order by created ASC") == \
        'project = "QCH" AND (status = Open) order by created ASC'
