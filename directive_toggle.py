#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerDirective [<text> | list | remove <n> | clear]` implementation.

Records STANDING instructions for the reviewer in THIS project. Unlike reviewerAgent.md --
which the reviewer reads exactly once per Claude session, when its session is briefed -- a
directive is re-read from disk and injected into every reviewer prompt, so it takes effect
on the very next commit/question/stop with no re-brief and no restart.

Use it for facts and authorizations the reviewer keeps re-litigating:

    /reviewerDirective Opus 4.6 is authorized for the judging process.

Directives persist ACROSS sessions, which is what distinguishes them from the operator
transcript relay (the operator's typed words, which Claude Code stamps as human input and
the reviewer receives automatically). Because a directive is recorded through a command
line rather than stamped by the harness, the reviewer is told to weigh it as the softer of
the two if they ever conflict.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _list(proj):
    items = rl.load_directives(proj)
    if not items:
        print(
            "reviewerDirective: no operator directives for this project. Add one with "
            "`/reviewerDirective <text>`."
        )
        return 0
    print("reviewerDirective: {} directive(s) in force for this project:".format(len(items)))
    for i, d in enumerate(items, 1):
        stamp = " [{}]".format(d["added"]) if d.get("added") else ""
        print("  {}.{} {}".format(i, stamp, d["text"]))
    return 0


def main(argv):
    proj = os.getcwd()
    args = [a for a in argv[1:] if a.strip()]

    if not args or (len(args) == 1 and args[0].lower() == "list"):
        return _list(proj)

    verb = args[0].lower()

    if verb == "clear" and len(args) == 1:
        n = rl.clear_directives(proj)
        rl.log("[directive] cleared {} for {}".format(n, rl.canonical_project_key(proj)))
        print("reviewerDirective: cleared {} directive(s) for this project.".format(n))
        return 0

    if verb == "remove":
        if len(args) != 2:
            print(
                "reviewerDirective: ERROR - `remove` takes one number, e.g. "
                "`/reviewerDirective remove 2` (see `/reviewerDirective list`).",
                file=sys.stderr,
            )
            return 2
        entry = rl.remove_directive(proj, args[1])
        if entry is None:
            print(
                "reviewerDirective: ERROR - no directive numbered {!r}. Run "
                "`/reviewerDirective list` to see the current numbering.".format(args[1]),
                file=sys.stderr,
            )
            return 2
        rl.log("[directive] removed {!r}".format(rl.short(entry["text"], 200)))
        print("reviewerDirective: removed {!r}. {} remaining.".format(
            entry["text"], len(rl.load_directives(proj))))
        return 0

    # Anything else is the directive itself. The skill passes $ARGUMENTS unquoted, so the
    # operator's words arrive as separate argv entries -- rejoin them into one directive.
    text = " ".join(args)
    try:
        entry = rl.add_directive(proj, text)
    except ValueError as e:
        print("reviewerDirective: ERROR - {}".format(e), file=sys.stderr)
        return 2
    rl.log("[directive] added {!r} for {}".format(
        rl.short(entry["text"], 200), rl.canonical_project_key(proj)))
    print(
        "reviewerDirective: recorded {!r} -- the reviewer receives it on every commit, "
        "question and stop from the next hook invocation (no re-brief needed). "
        "`/reviewerDirective list` to review, `remove <n>` or `clear` to retract.".format(
            entry["text"]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
