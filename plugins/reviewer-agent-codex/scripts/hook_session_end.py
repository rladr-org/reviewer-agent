#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""SessionEnd: remove liveness markers for the completed root session."""

import json
import sys

import _bootstrap  # noqa: F401
from reviewer_codex import liveness, state


def main():
    data = json.loads(sys.stdin.read() or "{}")
    session_id = state.session_key(data)
    liveness.clear_session(session_id)
    state.clear_session_artifacts(session_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
