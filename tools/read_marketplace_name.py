#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Print the local Codex marketplace name that `codex plugin add` needs.

The reinstall step of the Codex release procedure is
`codex plugin add reviewer-agent-codex@<marketplace-name>`, and that name comes from the
installed marketplace manifest rather than from anything in this repository. The plugin-creator
skill ships a script for it; that skill is local, system-installed tooling, so CI and outside
contributors cannot rely on it. This wrapper prefers it when present and otherwise reads the
same field from the same file.

  python3 tools/read_marketplace_name.py
  python3 tools/read_marketplace_name.py --marketplace-path /path/to/marketplace.json
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

PLUGIN_CREATOR = (
    Path.home() / ".codex" / "skills" / ".system" / "plugin-creator" / "scripts"
    / "read_marketplace_name.py"
)


def default_marketplace_path():
    return Path.home() / ".agents" / "plugins" / "marketplace.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--marketplace-path",
        default=str(default_marketplace_path()),
        help="path to marketplace.json (default: the personal marketplace under $HOME)",
    )
    args = parser.parse_args()

    if PLUGIN_CREATOR.is_file():
        result = subprocess.run(
            [sys.executable, str(PLUGIN_CREATOR), "--marketplace-path", args.marketplace_path],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(result.stdout.strip())
            return 0

    path = Path(args.marketplace_path).expanduser()
    try:
        name = json.loads(path.read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError) as error:
        print("could not read {}: {}".format(path, error), file=sys.stderr)
        return 1
    if not name:
        print("no top-level 'name' in {}".format(path), file=sys.stderr)
        return 1
    print(name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
