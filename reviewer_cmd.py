#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Single dispatcher behind the six CONSTRAINED reviewer-agent skill commands.

Collapsing six distinct toggle-script names to one entry point lets an operator add exactly ONE
scoped permission rule -- `Bash(python3 "<plugin>/reviewer_cmd.py":*)` -- instead of one per script.

Each skill's bang passes `"$ARGUMENTS"` QUOTED so a mistyped space/metacharacter in a constrained
arg does not break bash. Quoting makes a multi-token arg such as `on acceptRisk` arrive as ONE
string, so this re-splits on whitespace before delegating to the toggle's existing `main()` -- plain
`.split()`, which round-trips for token args and does not choke on an apostrophe. The dispatcher does
NOT re-validate: each toggle keeps its own argument contract (e.g. the model validator permits a `/`
in a namespaced id), so validation cannot drift here.

The free-text `/reviewerDirective` command is deliberately NOT routed here -- free text cannot be
made shell-safe through a bang, so it is handled by the UserPromptSubmit hook (JSON stdin, no shell).
See docs/plans/reviewer_cmd_dispatcher.md.
"""
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_SUBCOMMANDS = {
    "agent": "toggle",
    "history": "reviewer_history_toggle",
    "model": "reviewer_model_toggle",
    "session": "reviewer_session_toggle",
    "stopcontinue": "stopcontinue_toggle",
    "watchdog": "stopcontinue_watchdog_toggle",
}


def main(argv):
    if len(argv) < 2 or argv[1] not in _SUBCOMMANDS:
        sys.stderr.write(
            "reviewer_cmd: ERROR - unknown or missing subcommand; expected one of: {}\n".format(
                ", ".join(sorted(_SUBCOMMANDS))
            )
        )
        return 2
    subcmd = argv[1]
    module = importlib.import_module(_SUBCOMMANDS[subcmd])
    tokens = " ".join(argv[2:]).split()
    return module.main([subcmd] + tokens)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
