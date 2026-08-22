#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Launch the bundled reviewer-agent MCP stdio server."""

import _bootstrap  # noqa: F401
from reviewer_codex.mcp_server import main


if __name__ == "__main__":
    raise SystemExit(main())
