# SPDX-License-Identifier: MPL-2.0
"""Put the plugin root on sys.path for hook entrypoints."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
