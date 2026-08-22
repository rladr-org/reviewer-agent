#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/stopContinue on [acceptRisk]|off` implementation.

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


_DISCLAIMER = Path(__file__).resolve().parent / "DISCLAIMER.md"


def _acceptance_required():
    print(
        "Before enabling stopContinue you must accept the risk as described in "
        "DISCLAIMER.md by invoking:\n/stopContinue on acceptRisk",
        file=sys.stderr,
    )
    return 1


def _on(proj, accept_risk=False):
    root = rl.canonical_project_key(proj)
    if not rl.is_git_repo(root):
        print(
            "stopContinue: ERROR - {} is not a git repository. Aborting.".format(root),
            file=sys.stderr,
        )
        return 1
    try:
        disclaimer_hash = rl.disclaimer_sha256(_DISCLAIMER)
        if not rl.risk_accepted(root, disclaimer_hash):
            if not accept_risk:
                return _acceptance_required()
            rl.record_risk_acceptance(
                root, disclaimer_hash, "/stopContinue on acceptRisk"
            )
    except OSError as error:
        print(
            "stopContinue: ERROR - could not record risk acceptance: {}".format(error),
            file=sys.stderr,
        )
        return 1
    rl.set_stopcontinue_active(root)
    rl.log("[stopcontinue] on root={}".format(root))
    print(
        "stopContinue: ON for {} (the reviewer keeps the agent working instead of stalling "
        "to ask).".format(root)
    )
    print(
        "stopContinue: WARNING - the supervisor may direct the developer agent to continue "
        "work or take operational actions without further confirmation when it concludes that "
        "the operator already authorized them. Its authorization judgment may be wrong. The "
        "developer agent acts with your tools, credentials, and permissions. Full terms: {}"
        .format(_DISCLAIMER)
    )
    return 0


def main(argv):
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    proj = rl.project_dir()
    if action == "on" and len(argv) in (2, 3):
        if len(argv) == 3 and argv[2] != "acceptRisk":
            print("usage: stopcontinue_toggle.py on [acceptRisk]|off", file=sys.stderr)
            return 2
        return _on(proj, accept_risk=len(argv) == 3)
    if action == "off" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        rl.clear_stopcontinue_active(root)
        rl.log("[stopcontinue] off root={}".format(root))
        print("stopContinue: OFF for {}".format(root))
        return 0
    print("usage: stopcontinue_toggle.py on [acceptRisk]|off", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
