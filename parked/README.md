# parked/

Code that is **not on the active path** but kept for a planned future direction.
Nothing here is imported by the CLI (`awto_riden.py`), the MCP server, or the
core modules — they each add the repo root to `sys.path` so they still run
standalone if you invoke them directly.

| File | What | Tracking |
|---|---|---|
| `riden_server.py` | Unix-socket daemon that owns the serial port and multiplexes it over JSON-lines (the "multi-instrument device hub" idea). | issue #7 |
| `riden_client.py` | Thin synchronous client for `riden_server.py`. | issue #7 |

The active architecture is **direct-serial**: `awto_riden.py` / `mcp/mcp_server.py`
talk to `RidenWorker` (`riden_daemon.py`) directly — no socket layer. Revisit the
socket server/client if/when a multi-instrument hub is built (see issue #7).
