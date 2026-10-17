---
name: reviewerModel
description: "Show or set the reviewerAgent's model, the commit-audit timeout, the codex streaming idle thresholds, and the reasoning effort. Model and effort follow this project's backend (codex, pi or claude, each with its own slots and level set); the timeouts are shared. Usage: /reviewerModel [<model> [<seconds>] | clear | timeout <seconds>|clear | firstOutputTimeout <seconds>|clear | idleTimeout <seconds>|clear | effort <level>|clear]"
argument-hint: <model> [<seconds>] | clear | timeout <seconds>|clear | firstOutputTimeout <seconds>|clear | idleTimeout <seconds>|clear | effort <level>|clear
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" model "$ARGUMENTS"`

The block above is the live output of the model script (it ran before you saw this).

Relay that result to the user, quoting it verbatim from the output -- one short line per
`reviewerModel:` line present (the model now in effect, the commit-audit timeout, the two codex
streaming idle thresholds, the reasoning effort, or the error). Add no commentary and take no
further action.

Forms: `/reviewerModel` shows every setting; `<model>` sets the model; `<model> <seconds>`
sets model and timeout; `timeout <seconds>` sets only the commit-audit budget;
`firstOutputTimeout <seconds>` and `idleTimeout <seconds>` set the codex --json hang-detection
thresholds (N1 time-to-first-event, N2 between-events); `effort <level>` sets only the reasoning
effort; `clear` drops the model override and leaves the rest; each subcommand's `clear`
(`timeout clear`, `firstOutputTimeout clear`, `idleTimeout clear`, `effort clear`) drops its own.

**Everything here is scoped to the project's backend**, which the command resolves for itself and
names in its output. On a `pi` project, `<model>`, `effort` and `clear` act on pi's own slots and
the display reports pi's values; on a `codex` or `claude` project they act on that backend's.
Nothing writes a slot the running reviewer would never read. The model and effort are global per
backend: they apply to every project that uses that backend, not only this one, from each
session's next reviewer call. A change also retires this project's reviewer sessions for this
Claude session, because codex warns that a session resumed on a different model may behave
worse; other sessions keep resuming theirs, on the new setting.

`timeout` is shared: one setting, applied by every backend. `firstOutputTimeout` and
`idleTimeout` tune only the codex commit audit's `--json` hang detection -- N1 kills a reviewer
that never produced any output, N2 one that fell silent mid-run. Idle kills retry while the total
audit budget remains, up to eight retries, before a fail-closed block. Set `idleTimeout` above
the slowest test run.

Levels differ by backend and the sets are NOT interchangeable:

- codex:  `none, minimal, low, medium, high, xhigh, max, ultra`
- pi:     `off, minimal, low, medium, high, xhigh, max` -- `off` instead of `none`, and no `ultra`
- claude: `low, medium, high, xhigh, max` -- no `none`, no `off`, no `minimal`, no `ultra`

An unset pi or claude level means `medium`, which is what those backends are actually sent, so a
bare `/reviewerModel` reports `medium` rather than "not overridden". Levels are additionally per-MODEL, so one the
current model does not support may still fail the call. Effort applies to the commit audit
only: the brief stays low (its reply is discarded) and the stop supervisor
keeps its own. A timeout still BLOCKS the commit (the gate is fail-closed) -- raising it only
buys the reviewer more time on a large diff.
