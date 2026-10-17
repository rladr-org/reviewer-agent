#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/stopContinue on [acceptRisk]|off|delay [<seconds>|status|clear]` implementation.

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


_USAGE = "usage: stopcontinue_toggle.py on [acceptRisk]|off|delay [<0..{}>|status|clear]".format(
    rl.DELAY_MAX_S)


def _delay(args):
    """`delay [<seconds>|status|clear]`: show or set how long a stop waits before its review.
    User-wide, like the commit timeout; it never turns stopContinue on."""
    value = args[0] if args else "status"
    number = value.isascii() and value.isdigit()
    if len(args) > 1 or not (value in ("status", "clear") or number):
        print("stopContinue: ERROR - delay expects a whole number of seconds from 0 to {}, "
              "status, or clear.".format(rl.DELAY_MAX_S), file=sys.stderr)
        return 2
    if number:
        if int(value) > rl.DELAY_MAX_S:
            print("stopContinue: ERROR - delay expects a whole number of seconds from 0 to "
                  "{}.".format(rl.DELAY_MAX_S), file=sys.stderr)
            return 2
        rl.set_stopcontinue_delay(int(value))
        rl.log("[stopcontinue] delay set {}s".format(int(value)))
    elif value == "clear":
        rl.clear_stopcontinue_delay()
        rl.log("[stopcontinue] delay cleared")
    seconds, source = rl.stopcontinue_delay_detail()
    print("stopContinue: delay {} seconds (from {}; applies to every project). The supervisor "
          "waits this long before reviewing a stop; replying during the wait skips the review."
          .format(seconds, source))
    return 0


def main(argv):
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    if action == "delay":
        return _delay(argv[2:])
    proj = rl.project_dir()
    if action == "on" and len(argv) in (2, 3):
        if len(argv) == 3 and argv[2] != "acceptRisk":
            print(_USAGE, file=sys.stderr)
            return 2
        return _on(proj, accept_risk=len(argv) == 3)
    if action == "off" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        rl.clear_stopcontinue_active(root)
        rl.log("[stopcontinue] off root={}".format(root))
        print("stopContinue: OFF for {}".format(root))
        return 0
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
