"""Shared pytest setup.

noc_mcp.config instantiates Settings() at module import, and noc_email/noc_password
have no defaults, so importing ANY module that reaches noc_mcp.config -- server.py
(via tests/fake_noc.py) and ssh_exec.py -- needs them present. conftest is imported
before test modules are collected, which makes this the only place that reliably runs
first. Mirrors services/mcp/cms/tests/conftest.py, which exists for the same reason.
"""

import os

os.environ.setdefault("NOC_EMAIL", "u@example.com")
os.environ.setdefault("NOC_PASSWORD", "p")
