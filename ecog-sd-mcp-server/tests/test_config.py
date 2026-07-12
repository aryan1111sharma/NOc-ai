def test_settings_defaults():
    from ecog_sd_mcp.config import Settings
    s = Settings()
    assert s.ecog_base_url == "https://ecog.atlassian.net"
    assert s.mcp_port == 8104
    assert s.ecog_login_enabled is True
    assert s.ecog_email == "" and s.ecog_password == ""
