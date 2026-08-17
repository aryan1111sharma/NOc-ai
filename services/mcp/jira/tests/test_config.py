def test_settings_defaults_and_required(monkeypatch):
    monkeypatch.setenv("JIRA_EMAIL", "svc@ecog.local")
    monkeypatch.setenv("JIRA_API_TOKEN", "abc123")
    from jira_mcp.config import Settings

    s = Settings()
    assert s.jira_email == "svc@ecog.local"
    assert s.jira_api_token == "abc123"
    assert s.jira_base_url == "https://ecog.atlassian.net"
    assert s.mcp_port == 8102
    assert s.mcp_transport == "stdio"
