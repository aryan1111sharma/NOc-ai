"""Unit + integration tests for the CMS device-command tools."""

import os

# Settings requires cms_user/cms_pass; provide them before importing config.
os.environ.setdefault("CMS_USER", "u")
os.environ.setdefault("CMS_PASS", "p")


def test_device_settings_defaults():
    from cms_mcp.config import settings
    assert settings.device_poll_timeout == 20.0
    assert settings.device_poll_interval == 2.0
    assert settings.device_pending_ttl_seconds == 120
    assert settings.device_audit_log_file is None
