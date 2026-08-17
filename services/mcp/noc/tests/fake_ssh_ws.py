"""In-process fake of the charger SSH WebSocket PTY, for tests.

Scenario is chosen by the last path segment of the ws URL:
  NORMAL -> emits output + __QMCP_EXIT_0__
  FAIL   -> emits output + __QMCP_EXIT_2__
  ANSI   -> emits ANSI-coded output + sentinel
  HUGE   -> emits >max_bytes of output + sentinel
  SLOW   -> never emits the sentinel (client should time out)
  ERR    -> replies {"type":"error"} instead of {"type":"connected"}
"""

import asyncio
import contextlib
import json

from websockets.asyncio.server import serve


async def _handler(ws):
    scenario = ws.request.path.rsplit("/", 1)[-1]
    await ws.recv()  # the connect handshake frame
    if scenario == "ERR":
        await ws.send(json.dumps({"type": "error", "message": "auth failed"}))
        return
    await ws.send(json.dumps({"type": "connected"}))
    async for raw in ws:
        msg = json.loads(raw)
        if msg.get("action") != "data" or "__QMCP_EXIT" not in msg.get("data", ""):
            continue
        if scenario == "SLOW":
            # Bounded so server teardown is fast; still far longer than the client's
            # sub-second command_timeout, so the client times out first (the point of SLOW).
            await asyncio.sleep(2)
            continue
        if scenario == "ANSI":
            out = "\x1b[32mroot@charger\x1b[0m:~# \r\nup 3 days\r\n__QMCP_EXIT_0__\r\n"
        elif scenario == "HUGE":
            out = ("x" * 250_000) + "\r\n__QMCP_EXIT_0__\r\n"
        elif scenario == "FAIL":
            out = "boom\r\n__QMCP_EXIT_2__\r\n"
        else:  # NORMAL
            out = "up 3 days, load 0.10\r\n__QMCP_EXIT_0__\r\n"
        await ws.send(json.dumps({"type": "data", "data": out}))
        return


@contextlib.asynccontextmanager
async def fake_ssh_server():
    """Yield the ws:// base URL of a running fake SSH server."""
    async with serve(_handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"
