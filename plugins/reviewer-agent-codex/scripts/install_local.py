#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Install this checkout through Codex's personal plugin marketplace."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from reviewer_codex.host_versions import install_warnings  # noqa: E402
from reviewer_codex.install import InstallError, install_local  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Create the marketplace entry without invoking codex plugin add.",
    )
    args = parser.parse_args()

    # Before touching anything: say so when the host is older than the versions this
    # release was validated against. A warning, not a refusal -- the floors are the oldest
    # versions exercised, not the oldest that can work.
    for warning in install_warnings():
        print("install_local.py: WARNING - {}".format(warning), file=sys.stderr)

    try:
        result = install_local(PLUGIN_ROOT, install=not args.prepare_only)
    except (InstallError, OSError) as exc:
        parser.exit(1, f"install_local.py: error: {exc}\n")

    action = "Prepared" if args.prepare_only else "Installed"
    print(f"{action} {result.plugin_id}")
    print(f"Marketplace: {result.marketplace_path}")
    print("Open an interactive Codex CLI session by running `codex`, then run /hooks and")
    print("trust the reviewer-agent-codex hooks. /hooks is not a Codex GUI command.")
    print("Then exit the CLI, fully quit and restart the Codex GUI, and start a new GUI chat")
    print("so it loads the trusted hooks and current plugin components.")
    print("Active-host use inside the CLI has not been validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
