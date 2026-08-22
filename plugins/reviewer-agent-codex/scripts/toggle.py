#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""CLI entrypoint for feature toggles."""

import _bootstrap  # noqa: F401
from reviewer_codex.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
