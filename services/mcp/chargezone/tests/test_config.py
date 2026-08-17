def test_settings_defaults_and_required(monkeypatch):
    monkeypatch.setenv("CZ_JIRA_EMAIL", "sprathore@quenchchargers.com")
    monkeypatch.setenv("CZ_JIRA_API_TOKEN", "abc123")
    from cz_jira_mcp.config import Settings

    s = Settings()
    assert s.cz_jira_email == "sprathore@quenchchargers.com"
    assert s.cz_jira_api_token == "abc123"
    assert s.cz_jira_base_url == "https://team-1604552506221.atlassian.net"
    assert s.project_key == "QCH"
    assert s.mcp_port == 8105
    assert s.mcp_transport == "stdio"
