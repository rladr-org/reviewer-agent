# SPDX-License-Identifier: MPL-2.0
"""Configure reviewer MCP tools as direct Codex calls without editing TOML."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional


DIRECT_TOOL_NAMESPACE = "mcp__reviewer_agent"
CONFIG_KEY = "features.code_mode.direct_only_tool_namespaces"


class HostConfigError(RuntimeError):
    """Raised when Codex cannot read or atomically update its configuration."""


def _codex_bin(home: Path) -> str:
    configured = os.environ.get("REVIEWER_CODEX_BIN")
    return configured or shutil.which("codex") or str(home / ".local" / "bin" / "codex")


def _send(process, message) -> None:
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _receive(process, request_id):
    for line in process.stdout:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if message.get("id") != request_id:
            continue
        if message.get("error"):
            raise HostConfigError(
                "Codex config RPC failed: {}".format(message["error"])
            )
        return message.get("result") or {}
    raise HostConfigError("Codex app-server ended before config RPC {}".format(request_id))


def _current_namespaces(config):
    try:
        values = config["features"]["code_mode"]["direct_only_tool_namespaces"]
    except (KeyError, TypeError):
        return []
    if not isinstance(values, list):
        raise HostConfigError("{} must be an array".format(CONFIG_KEY))
    return list(dict.fromkeys(value for value in values if isinstance(value, str)))


def ensure_direct_namespace(
    *,
    home: Optional[Path] = None,
    popen: Callable = subprocess.Popen,
) -> bool:
    """Add the reviewer namespace through Codex's atomic config RPC."""
    home = (home or Path.home()).expanduser().resolve()
    environment = dict(os.environ, CODEX_HOME=str(home / ".codex"))
    process = popen(
        [_codex_bin(home), "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=environment,
    )
    try:
        _send(
            process,
            {
                "method": "initialize",
                "id": 1,
                "params": {
                    # DELIBERATELY not the plugin release version, and exempt from the parity
                    # check in tools/release_versions.py. This field identifies the INSTALLER
                    # CLIENT to the Codex app-server during a protocol handshake; nothing
                    # user-facing reads it, and tying it to the release would imply the
                    # handshake changed whenever the plugin was published. Bump it only when
                    # this installer's own protocol usage changes.
                    "clientInfo": {
                        "name": "reviewer_agent_installer",
                        "title": "Reviewer Agent Installer",
                        "version": "0.1.0",
                    }
                },
            },
        )
        _receive(process, 1)
        _send(process, {"method": "initialized", "params": {}})
        _send(
            process,
            {
                "method": "config/read",
                "id": 2,
                "params": {"includeLayers": False},
            },
        )
        current = _current_namespaces(_receive(process, 2).get("config") or {})
        if DIRECT_TOOL_NAMESPACE in current:
            return False
        _send(
            process,
            {
                "method": "config/value/write",
                "id": 3,
                "params": {
                    "keyPath": CONFIG_KEY,
                    "value": current + [DIRECT_TOOL_NAMESPACE],
                    "mergeStrategy": "replace",
                },
            },
        )
        _receive(process, 3)
        return True
    finally:
        try:
            process.stdin.close()
        except (AttributeError, OSError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
