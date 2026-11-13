#!/usr/bin/env python3
"""Repository-root entry point for the Cyan Eclipse Cadence MCP server.

The implementation lives in mcp/server.py; this wrapper gives source
installations and directory inspectors one obvious stdio command:
``python3 server.py``.
"""
from mcp.server import main


if __name__ == "__main__":
    raise SystemExit(main())
