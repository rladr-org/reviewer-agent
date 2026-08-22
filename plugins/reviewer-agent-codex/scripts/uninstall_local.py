#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Remove this plugin from Codex, leaving every other plugin and setting alone.

Reverses `install_local.py`: asks Codex to remove the installed plugin, deletes its cache, its
entry in the personal marketplace, and the `~/plugins/<name>` symlink pointing at this checkout.
Nothing else in the marketplace, the plugin cache or the Codex configuration is touched.

Safe to run twice, and safe to run after a half-finished install: it reports what it removed
and what it deliberately left.

  python3 scripts/uninstall_local.py
  python3 scripts/uninstall_local.py --purge-state   # also delete retained verdicts and state

Retained verdicts are the user's data rather than installer output, so they survive unless
`--purge-state` is given. `$reviewer-agent-codex:reviewer-history clear` removes one
repository's review record without uninstalling anything.

One thing this cannot do for you: the installer added `mcp__reviewer_agent` to
`features.code_mode.direct_only_tool_namespaces` in your Codex config through Codex's own
config RPC. Removing an entry from a shared array is not safe to automate against a running
host, so this prints the exact edit to make instead.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from reviewer_codex.host_config import CONFIG_KEY, DIRECT_TOOL_NAMESPACE  # noqa: E402
from reviewer_codex.uninstall import uninstall_local  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--purge-state",
        action="store_true",
        help="also delete retained verdicts, reviewer sessions and per-project settings",
    )
    args = parser.parse_args()

    result = uninstall_local(PLUGIN_ROOT, purge_state=args.purge_state)

    if result.removed:
        print("Removed:")
        for item in result.removed:
            print("  - {}".format(item))
    else:
        print("Nothing to remove; this plugin is not installed here.")
    if result.warnings:
        print("\nLeft in place:")
        for item in result.warnings:
            print("  - {}".format(item))
    print(
        "\nOne manual step remains. If nothing else needs it, remove {!r} from {} in your Codex "
        "configuration; leave the other namespaces alone.".format(
            DIRECT_TOOL_NAMESPACE, CONFIG_KEY
        )
    )
    if not args.purge_state:
        print(
            "Retained verdicts and reviewer state were kept. Re-run with --purge-state to "
            "delete them."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
