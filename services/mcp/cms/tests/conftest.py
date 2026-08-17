"""Shared pytest setup.

cms_mcp.config instantiates Settings() at module import, and cms_user/cms_pass have
no defaults, so importing ANY module under test needs them present. conftest is
imported before test modules are collected, which makes this the only place that
reliably runs first -- previously test_device_commands.py set them at import time and
every other test file worked only because it happened to sort alphabetically after it.
"""

import os

os.environ.setdefault("CMS_USER", "u")
os.environ.setdefault("CMS_PASS", "p")
