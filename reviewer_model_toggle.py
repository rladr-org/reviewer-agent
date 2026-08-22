#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerModel [<model> [<seconds>] | clear | timeout <secs>|clear | effort <level>|clear]`.

Three knobs on the reviewer, each a runtime property tuned per machine rather than per project.
No argument shows all three with their sources.

MODEL and EFFORT are stored PER BACKEND and this command scopes both to whichever backend the
project resolves to, naming it in every line it prints. Writing the codex slot from a pi project
produced a success message for a change the running reviewer would never read.

- MODEL: which model EVERY reviewer call uses, applied from the very next hook invocation
  including resumed sessions (both CLIs accept a per-turn model override).
- TIMEOUT: the commit-audit budget, and the one setting SHARED by every backend. The gate stays
  fail-CLOSED, so this buys time, not mercy.
- EFFORT: reasoning depth. The backends have DIFFERENT valid sets -- codex `none..ultra`,
  pi `off..max`, claude `low..max` -- so each is validated against its own. On codex it covers the
  commit audit and question answers, deliberately NOT the brief (its reply is discarded) and not
  the stop supervisor (own knob, runs every turn end). On pi it is the `--thinking` level and on
  claude the `--effort` flag, both defaulting to `medium` because the flag is always sent.

`clear` means the MODEL override alone; the timeout and effort have their own `clear`.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _codex_default():
    """Best-effort read of the codex CLI's own default model, for display only."""
    try:
        text = (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _codex_default_effort():
    """Best-effort read of the codex CLI's own reasoning effort, for display only."""
    try:
        text = (Path.home() / ".codex" / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else None


def _effort_line():
    # Source from the resolver, never re-derived here -- the model and timeout displays each
    # shipped this defect once: an INVALID env var is skipped by resolution, so calling any
    # non-empty one the source sends the operator to unset something already being ignored.
    backend = _backend()
    effort, source = rl.reviewer_effort_detail(backend)
    if effort:
        return "reviewerModel: reasoning effort {} (backend {}, from {})".format(
            effort, backend, source)
    # Only codex has an inherit case: it passes no override at all and lets the CLI decide. pi
    # always sends the flag, so its resolver returns a level rather than None.
    default = _codex_default_effort()
    suffix = " (currently {})".format(default) if default else ""
    return (
        "reviewerModel: reasoning effort not overridden -> codex CLI default from "
        "~/.codex/config.toml{}".format(suffix)
    )


def _set_effort(token):
    backend = _backend()
    if token.lower() == "clear":
        rl.clear_backend_effort(backend)
        rl.log("[model] reasoning effort cleared (backend {})".format(backend))
        # Report what resolution ACTUALLY yields now, rather than asserting a return to the
        # default: `clear` removes the persistent slot, but the env var outranks it, so with
        # REVIEWER_<BACKEND>_EFFORT set the level does not change at all. Announcing a default
        # that is not in force is the same misreporting this whole change removes.
        effort, source = rl.reviewer_effort_detail(backend)
        if effort:
            tail = "now {} (from {})".format(effort, source)
        else:
            tail = "inherit the codex CLI default from ~/.codex/config.toml"
        print("reviewerModel: reasoning effort slot cleared (backend {}) -> {}".format(
            backend, tail))
        return 0
    if not rl.is_valid_backend_effort(backend, token):
        # Rejected rather than passed through: an unrecognised level is not tolerated the way
        # an unrecognised service_tier is -- the CLI FAILS the call, and the commit gate is
        # fail-CLOSED, so this would block every commit until traced back here. The list shown
        # is THIS backend's: pi takes `off` and no `ultra`, codex the reverse.
        print(
            "reviewerModel: ERROR - invalid reasoning effort {!r} for backend {} -- expected "
            "one of: {}".format(token, backend, ", ".join(rl.reviewer_efforts(backend))),
            file=sys.stderr,
        )
        return 2
    rl.set_backend_effort(backend, token)
    rl.log("[model] reasoning effort set {} (backend {})".format(token, backend))
    print(
        "reviewerModel: reasoning effort {} (backend {}) -- applies from the next hook "
        "invocation. /reviewerModel effort clear reverts.".format(token, backend)
    )
    return 0


def _timeout_line():
    # The SOURCE comes from the resolver, never re-derived here: restating the precedence made
    # this print "from env" for a value the env had not supplied (an invalid env var is skipped).
    secs, source = rl.commit_review_timeout_detail()
    prefix = "" if source == "default" else "from "
    return "reviewerModel: commit-audit timeout {}s ({}{})".format(secs, prefix, source)


def _backend():
    """The backend THIS project resolves to. Every model/effort operation is scoped to it:
    writing the codex slot from a pi project produced a success line for a change the running
    reviewer would never read."""
    return rl.reviewer_backend(rl.project_dir())


def _inherit_phrase(backend):
    """What "no override" falls back to, per backend -- they are different tools with different
    configuration files, so one sentence cannot describe all three."""
    if backend == "codex":
        return "codex CLI default from ~/.codex/config.toml"
    if backend == "claude":
        return "the claude CLI's own default model"
    return "pi's own default model"


def _show():
    # Source from the resolver, never re-derived here -- see codex_model_detail().
    backend = _backend()
    model, source = rl.reviewer_model_detail(backend)
    if model:
        print("reviewerModel: {} (backend {}, from {})".format(model, backend, source))
    else:
        suffix = ""
        if backend == "codex":
            default = _codex_default()
            suffix = " (currently {})".format(default) if default else ""
        print(
            "reviewerModel: no override (backend {}) -> {}{}".format(
                backend, _inherit_phrase(backend), suffix)
        )
    print(_timeout_line())
    print(_effort_line())
    return 0


# Claude Code's documented default when a hook declares no `timeout`.
_CC_DEFAULT_HOOK_TIMEOUT = 600


def _hook_timeout_ceiling():
    """(seconds, where) after which Claude Code CANCELS the hook carrying the review, or
    (None, "") if it cannot be determined.

    This is a hard ceiling on the review budget, and exceeding it is not merely ineffective: per
    the Claude Code hook docs, exit 2 blocks a tool call and "any other exit code is a
    non-blocking error". A cancelled hook is neither, so once Claude Code cancels the gate the
    commit PROCEEDS UNREVIEWED. A budget above this ceiling converts a fail-closed gate into a
    fail-open one, which is why it is refused rather than clamped.

    Read from the user settings the hooks actually run from, falling back to this plugin's own
    manifest. Only Bash hooks that carry the review are considered -- an unrelated Bash hook
    being cancelled says nothing about the gate.

    BOTH spellings of a hook are searched, and that is not defensive coding. `hooks.json` ships
    the EXEC form -- `"command": "python3"` with the script in `args` -- while an older
    standalone install wires a single command STRING in settings.json. Searching only `command`
    matched nothing in the shipped manifest, so on an install with no legacy settings entry this
    returned "no ceiling" and the refusal below never fired: a budget at or above the hook's own
    timeout was accepted, when the whole point of the check is to refuse it. It went unnoticed
    because the author's machine still carries the legacy string form and satisfied the lookup;
    CI, which has no `~/.claude` at all, failed on every platform the first time it ran."""
    candidates = []
    settings = Path.home() / ".claude" / "settings.json"
    manifest = Path(__file__).resolve().parent / "hooks" / "hooks.json"
    for path in (settings, manifest):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = ((data.get("hooks") or {}).get("PreToolUse") or [])
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
                continue
            for hook in entry.get("hooks") or []:
                args = (hook or {}).get("args")
                command = " ".join(
                    [str((hook or {}).get("command") or "")]
                    + ([str(a) for a in args] if isinstance(args, (list, tuple)) else [])
                )
                if "commit_hook.py" not in command and "bash_gate.py" not in command:
                    continue
                value = hook.get("timeout")
                candidates.append((
                    value if isinstance(value, int) and value > 0 else _CC_DEFAULT_HOOK_TIMEOUT,
                    str(path)))
        if candidates:
            break                      # user settings win outright; do not mix the two sources
    if not candidates:
        return None, ""
    return min(candidates)             # the earliest cancellation is the one that binds


def _parse_timeout(token):
    """(seconds, error). Rejects an out-of-range value outright rather than clamping: this is an
    explicit operator instruction, and silently substituting a different number would leave
    them believing a budget they do not have. `commit_review_timeout()` still clamps, for
    hand-edited files and env vars that bypass this path."""
    try:
        secs = int(token)
    except (TypeError, ValueError):
        return None, "invalid timeout {!r} -- expected whole seconds, e.g. 1800".format(token)
    if secs < rl.COMMIT_TIMEOUT_FLOOR:
        return None, "timeout {}s is below the {}s floor (a tiny timeout would make the " \
                     "fail-closed commit gate block EVERY commit)".format(
                         secs, rl.COMMIT_TIMEOUT_FLOOR)
    ceiling, where = _hook_timeout_ceiling()
    if ceiling is not None and secs >= ceiling:
        return None, (
            "timeout {}s is at or above the {}s hook timeout in {}, so Claude Code would CANCEL "
            "the review before it finished. A cancelled hook is a non-blocking error, not a "
            "block, so the commit would then proceed UNREVIEWED -- the gate would fail OPEN. "
            "Raise that hook's `timeout` first (keep it comfortably above the review budget), "
            "then set this.".format(secs, ceiling, where))
    return secs, None


def _set_timeout(token):
    if token.lower() == "clear":
        rl.clear_commit_timeout()
        rl.log("[model] commit timeout cleared")
        print("reviewerModel: commit-audit timeout cleared -> default {}s".format(
            rl.DEFAULT_COMMIT_TIMEOUT))
        return 0
    secs, err = _parse_timeout(token)
    if err:
        print("reviewerModel: ERROR - " + err, file=sys.stderr)
        return 2
    rl.set_commit_timeout(secs)
    rl.log("[model] commit timeout set {}s".format(secs))
    print(
        "reviewerModel: commit-audit timeout {}s -- applies from the next commit. The gate "
        "stays fail-CLOSED, so a timeout still BLOCKS the commit; this only buys the "
        "reviewer more time. /reviewerModel timeout clear reverts.".format(secs)
    )
    return 0


def main(argv):
    args = [a.strip() for a in argv[1:] if a.strip()]
    if len(args) > 2:
        print(
            "reviewerModel: ERROR - expected `<model>`, `clear`, `<model> <seconds>`, "
            "`timeout <seconds>|clear`, or `effort <level>|clear`, got: {!r}".format(args),
            file=sys.stderr,
        )
        return 2
    if not args:
        return _show()

    # `timeout ...` addresses the timeout ALONE and leaves the model untouched.
    if args[0].lower() == "timeout":
        if len(args) != 2:
            print("reviewerModel: ERROR - `timeout` needs a value: "
                  "/reviewerModel timeout <seconds>|clear", file=sys.stderr)
            return 2
        return _set_timeout(args[1])

    # `effort ...` likewise. Checked BEFORE the model branch: a bare `/reviewerModel effort`
    # would otherwise be a well-formed model name and would silently persist "effort" as the
    # model, whose first use is a failed -- and fail-CLOSED -- commit audit.
    if args[0].lower() == "effort":
        if len(args) != 2:
            # THIS backend's levels: printing codex's list on a pi project would name `ultra`,
            # which pi rejects, and omit `off`, which it accepts.
            print("reviewerModel: ERROR - `effort` needs a value: "
                  "/reviewerModel effort <level>|clear (levels for backend {}: {})".format(
                      _backend(), ", ".join(rl.reviewer_efforts(_backend()))), file=sys.stderr)
            return 2
        return _set_effort(args[1])

    arg = args[0]
    if arg.lower() == "clear":
        if len(args) != 1:
            print("reviewerModel: ERROR - `clear` takes no further arguments (use "
                  "`/reviewerModel timeout clear` for the timeout)", file=sys.stderr)
            return 2
        # Deliberately does NOT clear the timeout: `clear` has always meant "drop the model
        # override", and the timeout is a separate, independently-set knob.
        backend = _backend()
        rl.clear_backend_model(backend)
        rl.log("[model] override cleared (backend {})".format(backend))
        # Same reason as effort clear: REVIEWER_<BACKEND>_MODEL outranks the slot, so with it
        # set nothing changes and reporting a fallback would be false.
        model, source = rl.reviewer_model_detail(backend)
        tail = ("now {} (from {})".format(model, source) if model
                else _inherit_phrase(backend))
        print("reviewerModel: model slot cleared (backend {}) -> {}".format(backend, tail))
        return 0

    # Validate EVERYTHING before writing ANYTHING: a rejected command must not leave the
    # model applied and the timeout not, which is worse than doing nothing at all.
    if not rl.is_valid_codex_model(arg):
        print(
            "reviewerModel: ERROR - invalid model name {!r} (single token, "
            "[A-Za-z0-9._:/-] only)".format(arg),
            file=sys.stderr,
        )
        return 2
    secs = None
    if len(args) == 2:
        secs, err = _parse_timeout(args[1])
        if err:
            print("reviewerModel: ERROR - " + err + " (the model was NOT changed)",
                  file=sys.stderr)
            return 2

    backend = _backend()
    rl.set_backend_model(backend, arg)
    rl.log("[model] set {} (backend {})".format(arg, backend))
    print(
        "reviewerModel: {} (backend {}) -- applies to reviewer calls from the next hook "
        "invocation. A model name {} does not recognize will make reviews fail "
        "(commit gate fail-closed); /reviewerModel clear reverts.".format(arg, backend, backend)
    )
    if secs is not None:
        rl.set_commit_timeout(secs)
        rl.log("[model] commit timeout set {}s".format(secs))
        print("reviewerModel: commit-audit timeout {}s -- applies from the next "
              "commit.".format(secs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
