"""Each module here owns one platform-introspection capability.

Contract every module follows: expose ``register(mcp, settings)``, which adds that
capability's tool(s) to ``mcp``. ``server.py`` just imports the module and calls
``register`` — adding a new capability later (Docker, CI/CD, logs, metrics, ...) means
adding one new module here and one line in ``server.py``'s ``_TOOL_MODULES``, never
restructuring an existing tool.
"""
