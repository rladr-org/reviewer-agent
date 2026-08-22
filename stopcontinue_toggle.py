#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/stopContinue on|off` implementation.

Toggles a SEPARATE per-project flag that enables the Stop hook (stop_hook.py): when the
audited agent ends its turn with a prose question instead of calling AskUserQuestion, the
reviewer session on the selected backend answers it and the agent continues. Independent of
/reviewerAgent;
no brief is run here (the Stop hook establishes/reuses the reviewer session lazily).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _on(proj):
    root = rl.canonical_project_key(proj)
    if not rl.is_git_repo(root):
        print(
            "stopContinue: ERROR - {} is not a git repository. Aborting.".format(root),
            file=sys.stderr,
        )
        return 1
    rl.set_stopcontinue_active(root)
    rl.log("[stopcontinue] on root={}".format(root))
    print(
        "stopContinue: ON for {} (the reviewer keeps the agent working instead of stalling "
        "to ask).".format(root)
    )
    disclaimer = Path(__file__).resolve().parent / "DISCLAIMER.md"
    print(
        "stopContinue: WARNING - the supervisor may direct the developer agent to continue "
        "work or take operational actions without further confirmation when it concludes that "
        "the operator already authorized them. Its authorization judgment may be wrong. The "
        "developer agent acts with your tools, credentials, and permissions. Full terms: {}"
        .format(disclaimer)
    )
    return 0


def main(argv):
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    proj = rl.project_dir()
    if action == "on":
        return _on(proj)
    if action == "off":
        root = rl.canonical_project_key(proj)
        rl.clear_stopcontinue_active(root)
        rl.log("[stopcontinue] off root={}".format(root))
        print("stopContinue: OFF for {}".format(root))
        return 0
    print("usage: stopcontinue_toggle.py on|off", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
