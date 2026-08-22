#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from reviewer_codex.cli import main  # noqa: E402

raise SystemExit(main(["reviewer", *(sys.argv[1:] or ["status"])]))
