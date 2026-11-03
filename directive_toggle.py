#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerDirective [<text> | list | remove <n> | clear]` implementation, and the same for
`/stopContinueDirective` (`kind="stop"`).

Records STANDING instructions for the reviewer in THIS project. Unlike reviewerAgent.md --
which the reviewer reads exactly once per Claude session, when its session is briefed -- a
directive is re-read from disk and injected into every reviewer prompt, so it takes effect
on the very next commit/question/stop with no re-brief and no restart.

Use it for facts and authorizations the reviewer keeps re-litigating:

    /reviewerDirective Opus 4.6 is authorized for the judging process.

A stop directive is a separate list that reaches only the stop supervisor: rules about when the
agent may stop or must keep working, which have no business in a commit audit
(docs/plans/stopcontinue_directive.md):

    /stopContinueDirective Items on HANDOVER.md's approved list are approved; do not stop between them.

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

_COMMAND = {rl.DIRECTIVE_KIND_REVIEWER: "reviewerDirective",
            rl.DIRECTIVE_KIND_STOP: "stopContinueDirective"}
_NONE_YET = {rl.DIRECTIVE_KIND_REVIEWER: "no operator directives",
             rl.DIRECTIVE_KIND_STOP: "no stop directives"}
_REACHES = {rl.DIRECTIVE_KIND_REVIEWER: "the reviewer receives it on every commit, question and stop",
            rl.DIRECTIVE_KIND_STOP: "the stop supervisor receives it on every stop review"}


def _list_message(proj, kind):
    cmd = _COMMAND[kind]
    items = rl.load_directives(proj, kind)
    if not items:
        return "{}: {} for this project. Add one with `/{} <text>`.".format(cmd, _NONE_YET[kind], cmd)
    lines = ["{}: {} directive(s) in force for this project:".format(cmd, len(items))]
    for i, d in enumerate(items, 1):
        stamp = " [{}]".format(d["added"]) if d.get("added") else ""
        lines.append("  {}.{} {}".format(i, stamp, d["text"]))
    return "\n".join(lines)


def run(args, proj, kind=rl.DIRECTIVE_KIND_REVIEWER):
    """Execute a reviewerDirective (or, with `kind="stop"`, a stopContinueDirective) invocation
    under an EXPLICIT project and return (exit_code, message) WITHOUT printing. `proj` is passed in
    rather than read from os.getcwd() so the UserPromptSubmit hook records under the payload cwd --
    the authoritative repository -- not the hook process's own directory. `args` is a token list
    (argv-style); the caller owns tokenization, so the hook can pass free-form directive text as a
    single verbatim element while the CLI passes shell-split words."""
    cmd = _COMMAND[kind]
    args = [a for a in args if a and a.strip()]

    if not args or (len(args) == 1 and args[0].lower() == "list"):
        return (0, _list_message(proj, kind))

    verb = args[0].lower()

    if verb == "clear" and len(args) == 1:
        n = rl.clear_directives(proj, kind)
        rl.log("[directive] cleared {} {} for {}".format(n, kind, rl.canonical_project_key(proj)))
        return (0, "{}: cleared {} directive(s) for this project.".format(cmd, n))

    if verb == "remove":
        if len(args) != 2:
            return (2, "{0}: ERROR - `remove` takes one number, e.g. "
                       "`/{0} remove 2` (see `/{0} list`).".format(cmd))
        entry = rl.remove_directive(proj, args[1], kind)
        if entry is None:
            return (2, "{0}: ERROR - no directive numbered {1!r}. Run "
                       "`/{0} list` to see the current numbering.".format(cmd, args[1]))
        rl.log("[directive] removed {} {!r}".format(kind, rl.short(entry["text"], 200)))
        return (0, "{}: removed {!r}. {} remaining.".format(
            cmd, entry["text"], len(rl.load_directives(proj, kind))))

    # Anything else is the directive itself. The caller supplies tokenization: the hook passes the
    # operator's free text as a single verbatim element (injection-proof, never shell-parsed); the
    # CLI passes shell-split words.
    text = " ".join(args)
    try:
        entry = rl.add_directive(proj, text, kind)
    except ValueError as e:
        return (2, "{}: ERROR - {}".format(cmd, e))
    rl.log("[directive] added {} {!r} for {}".format(
        kind, rl.short(entry["text"], 200), rl.canonical_project_key(proj)))
    return (0,
            "{0}: recorded {1!r} -- {2} from the next hook invocation (no re-brief needed). "
            "`/{0} list` to review, `remove <n>` or `clear` to retract.".format(
                cmd, entry["text"], _REACHES[kind]))


def main(argv):
    code, message = run(argv[1:], os.getcwd())
    print(message, file=(sys.stdout if code == 0 else sys.stderr))
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
