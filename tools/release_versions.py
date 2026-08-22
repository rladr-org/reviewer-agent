#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""The release identity, every file that declares it, and the parity check over them.

One release, four spellings, and a check that they mean the same thing:

  1.2.0-beta.1              the canonical SemVer identity (Claude manifests, MCP server)
  v1.2.0-beta.1             the Git tag; normalized by dropping the leading `v`
  1.2.0-beta.1+codex.<UTC>  the Codex plugin, whose build metadata is a cachebuster the host
                            needs in order to notice a reinstall; normalized by dropping `+...`
  1.2.0b1                   PEP 440, which is what a Python package version must be; mapped to
                            the SemVer prerelease explicitly rather than by string surgery

One declared version is deliberately NOT part of the release identity:
`reviewer_codex/host_config.py` sends `clientInfo.version` to the Codex app-server during
installation. That field identifies the installer CLIENT in a protocol handshake, not the
plugin, and nothing user-facing reads it. It is exempt by decision, recorded here so the check
does not flag it every time someone looks.

Run `python3 tools/release_versions.py` to see every declared version and whether they agree.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The canonical release identity. Everything else is a spelling of this.
RELEASE = "1.2.0-beta.1"
PEP440_RELEASE = "1.2.0b1"
TAG = "v" + RELEASE

_PEP440_PRERELEASE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?P<kind>a|b|rc)(?P<number>\d+)$")
_PRERELEASE_WORD = {"a": "alpha", "b": "beta", "rc": "rc"}


def normalize(version):
    """Reduce any of the four spellings to the canonical SemVer identity.

    Three transformations, each for one real spelling:
      - a leading `v`, because Git tags carry one and manifests do not;
      - `+build` metadata, because SemVer defines it as excluded from precedence and the Codex
        cachebuster lives there;
      - PEP 440 prerelease shorthand (`1.2.0b1`), because Python packaging cannot express
        `-beta.1` and a naive string compare would call the two versions different forever.
    """
    text = str(version or "").strip()
    if text.startswith("v"):
        text = text[1:]
    text = text.split("+", 1)[0]
    match = _PEP440_PRERELEASE.match(text)
    if match:
        return "{}-{}.{}".format(
            match.group("base"),
            _PRERELEASE_WORD[match.group("kind")],
            match.group("number"),
        )
    return text


def _json_value(relative, *keys):
    data = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    for key in keys:
        data = data[key]
    return data


def _regex_value(relative, pattern):
    text = (ROOT / relative).read_text(encoding="utf-8")
    match = re.search(pattern, text, re.M)
    if not match:
        raise ValueError("no version found in {}".format(relative))
    return match.group(1)


def declared_versions():
    """Every declared version in the release, as {label: raw string}."""
    codex = "plugins/reviewer-agent-codex/"
    return {
        ".claude-plugin/plugin.json": _json_value(".claude-plugin/plugin.json", "version"),
        ".claude-plugin/marketplace.json metadata": _json_value(
            ".claude-plugin/marketplace.json", "metadata", "version"
        ),
        ".claude-plugin/marketplace.json plugin entry": _json_value(
            ".claude-plugin/marketplace.json", "plugins", 0, "version"
        ),
        codex + ".codex-plugin/plugin.json": _json_value(
            codex + ".codex-plugin/plugin.json", "version"
        ),
        codex + "pyproject.toml": _regex_value(
            codex + "pyproject.toml", r'^version\s*=\s*"([^"]+)"'
        ),
        codex + "reviewer_codex/__init__.py": _regex_value(
            codex + "reviewer_codex/__init__.py", r'^__version__\s*=\s*"([^"]+)"'
        ),
        codex + "reviewer_codex/mcp_server.py": _regex_value(
            codex + "reviewer_codex/mcp_server.py", r'^SERVER_VERSION\s*=\s*"([^"]+)"'
        ),
    }


def mismatches():
    """Every declared version whose normalized form is not the canonical release."""
    return {
        label: raw
        for label, raw in declared_versions().items()
        if normalize(raw) != RELEASE
    }


def main():
    declared = declared_versions()
    width = max(len(label) for label in declared)
    print("canonical release: {}  (tag {}, PEP 440 {})".format(RELEASE, TAG, PEP440_RELEASE))
    for label, raw in sorted(declared.items()):
        status = "ok " if normalize(raw) == RELEASE else "BAD"
        print("  {} {:<{}}  {}".format(status, label, width, raw))
    print("  exempt plugins/reviewer-agent-codex/reviewer_codex/host_config.py "
          "clientInfo.version (installer protocol client, not the plugin release)")
    bad = mismatches()
    if bad:
        print("\nFAIL: {} declared version(s) do not normalize to {}".format(len(bad), RELEASE))
        return 1
    print("\nPASS: every declared version normalizes to {}".format(RELEASE))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
