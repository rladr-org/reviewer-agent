#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Stop: continue Codex when liveness or the supervisor says work remains."""

import json
import sys
import traceback

import _bootstrap  # noqa: F401
from reviewer_codex import supervisor


def main():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        decision = supervisor.supervise(data)
        if decision:
            print(json.dumps(decision))
    except Exception:
        traceback.print_exc(file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
