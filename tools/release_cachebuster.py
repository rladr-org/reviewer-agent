#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Rewrite the Codex plugin's `+codex.<cachebuster>` build metadata.

Codex resolves an installed local plugin by version string, so reinstalling a plugin whose
version has not changed can serve the cached copy. The supported release procedure therefore
bumps a cachebuster in the build-metadata segment. That tooling ships with the plugin-creator
skill, which is a local, system-installed component: CI does not have it, and neither does an
outside contributor following the release procedure in this repository.

So this wrapper prefers the plugin-creator script when it is installed -- that is the supported
path and stays authoritative -- and otherwise performs the same documented transform itself:
keep everything before `+`, replace any build metadata with `+codex.<UTC timestamp>`.

  python3 tools/release_cachebuster.py                       # bump to the current UTC second
  python3 tools/release_cachebuster.py --cachebuster 12345   # a specific token (reproducible)
  python3 tools/release_cachebuster.py --print               # show the version, change nothing
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "reviewer-agent-codex"
MANIFEST = PLUGIN / ".codex-plugin" / "plugin.json"
PLUGIN_CREATOR = (
    Path.home() / ".codex" / "skills" / ".system" / "plugin-creator" / "scripts"
    / "update_plugin_cachebuster.py"
)
PREFIX = "codex"


def current_version():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]


def _utc_token():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")


def _rewrite_locally(cachebuster):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = str(manifest["version"]).split("+", 1)[0]
    manifest["version"] = "{}+{}.{}".format(base, PREFIX, cachebuster)
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest["version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cachebuster", help="token to embed (default: the current UTC second)")
    parser.add_argument(
        "--print", dest="show", action="store_true", help="print the version and exit"
    )
    args = parser.parse_args()

    if args.show:
        print(current_version())
        return 0

    cachebuster = args.cachebuster or _utc_token()
    if PLUGIN_CREATOR.is_file():
        result = subprocess.run(
            [sys.executable, str(PLUGIN_CREATOR), str(PLUGIN), "--cachebuster", cachebuster],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(result.stdout.strip() or current_version())
            return 0
        # Fall through rather than fail: the transform is simple and documented, and a release
        # must not depend on one machine's optional tooling staying installed.
        print(
            "plugin-creator tooling failed ({}); applying the documented transform "
            "locally".format(result.stderr.strip() or result.returncode),
            file=sys.stderr,
        )
    print(_rewrite_locally(cachebuster))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
