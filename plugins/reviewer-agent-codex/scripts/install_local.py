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
    print("trust any reviewer-agent-codex hooks requiring approval. /hooks is not a Codex GUI command.")
    print("GUI users: exit the CLI, fully quit and restart the Codex GUI, then start a fresh chat.")
    print("CLI users: start a fresh interactive CLI session to load the current plugin.")
    print("Upgrades preserve repository feature settings and risk-acceptance receipts.")
    print("Completion review runs only from Stop; Interrupt cancels active reviews.")
    print("Codex invokes stop_hook and interrupt_stop automatically; do not call them manually.")
    print("Verify the connected reviewer server, hook mappings, and actual review outcomes.")
    print("An absent continuation prompt does not prove a review ran; continuation feedback may be visible.")
    print("CLI stop supervision was exercised on macOS with Codex 0.154.0;")
    print("this does not establish CLI commit-gate or general terminal UI coverage.")
    print(f"Verification steps: {PLUGIN_ROOT / 'README.md'} (Post-install verification)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
