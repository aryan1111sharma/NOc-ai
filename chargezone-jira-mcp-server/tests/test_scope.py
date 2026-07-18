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


@pytest.mark.parametrize("cond", [
    "status = Open ORDER BY created ASC",
    "ORDER BY created DESC",
    '(x = 1 ORDER BY updated) OR (project != "QCH")',   # the 2nd-escape payload
    "status = Open order by created",                   # lowercase
])
def test_build_scoped_jql_rejects_order_by_in_conditions(cond):
    with pytest.raises(ValueError, match="ORDER BY"):
        build_scoped_jql(cond)


def test_build_scoped_jql_allows_order_by_inside_quotes():
    # 'order by' inside a quoted string is data, not a clause — must be allowed and wrapped.
    assert build_scoped_jql('text ~ "please order by priority"') == \
        'project = "QCH" AND (text ~ "please order by priority") ORDER BY updated DESC'


from cz_jira_mcp.server import _validate_order_by, _plevel, _field_str


def test_validate_order_by_default_and_whitelist():
    assert _validate_order_by("") == "ORDER BY updated DESC"
    assert _validate_order_by("created DESC") == "ORDER BY created DESC"
    assert _validate_order_by("cf[10120] ASC, assignee ASC") == "ORDER BY cf[10120] ASC, assignee ASC"
    assert _validate_order_by("ORDER BY updated DESC") == "ORDER BY updated DESC"  # strips leading kw

import pytest
@pytest.mark.parametrize("bad", ["updated); DROP", "a OR b", "created DESC) OR (1=1", "assignee ASC; evil"])
def test_validate_order_by_rejects_injection(bad):
    with pytest.raises(ValueError, match="order_by"):
        _validate_order_by(bad)

def test_build_scoped_jql_with_order_by():
    from cz_jira_mcp.server import build_scoped_jql
    assert build_scoped_jql("statusCategory != Done", order_by="cf[10120] ASC") == \
        'project = "QCH" AND (statusCategory != Done) ORDER BY cf[10120] ASC'
    assert build_scoped_jql("", order_by="assignee ASC") == 'project = "QCH" ORDER BY assignee ASC'

def test_plevel_and_field_str():
    assert _plevel({"customfield_10120": {"value": "Priority Level - 02"}}) == "P2"
    assert _plevel({"customfield_10120": None}) == "Unassigned"
    assert _plevel({}) == "Unassigned"
    assert _field_str({"customfield_10110": "CG0003"}, "customfield_10110") == "CG0003"
    assert _field_str({"x": {"value": "Cat"}}, "x") == "Cat"
    assert _field_str({}, "x") is None
