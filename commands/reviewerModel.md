---
description: "Show or set the reviewerAgent's model, the commit-audit timeout, and the reasoning effort. Model and effort follow this project's backend (codex, pi or claude, each with its own slots and level set); the timeout is shared. Usage: /reviewerModel [<model> [<seconds>] | clear | timeout <seconds>|clear | effort <level>|clear]"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_model_toggle.py" $ARGUMENTS`

The block above is the live output of the model script (it ran before you saw this).

Relay that result to the user, quoting it verbatim from the output -- one short line per
`reviewerModel:` line present (the model now in effect, the commit-audit timeout, the reasoning
effort, or the error). Add no commentary and take no further action.

Forms: `/reviewerModel` shows all three settings; `<model>` sets the model; `<model> <seconds>`
sets model and timeout; `timeout <seconds>` sets only the commit-audit budget;
`effort <level>` sets only the reasoning effort; `clear` drops the model override and leaves
the other two; `timeout clear` and `effort clear` drop theirs.

**Everything here is scoped to the project's backend**, which the command resolves for itself and
names in its output. On a `pi` project, `<model>`, `effort` and `clear` act on pi's own slots and
the display reports pi's values; on a `codex` or `claude` project they act on that backend's.
Nothing writes a slot the running reviewer would never read.

`timeout` is shared: one setting, applied by every backend.

Levels differ by backend and the sets are NOT interchangeable:

- codex:  `none, minimal, low, medium, high, xhigh, max, ultra`
- pi:     `off, minimal, low, medium, high, xhigh, max` -- `off` instead of `none`, and no `ultra`
- claude: `low, medium, high, xhigh, max` -- no `none`, no `off`, no `minimal`, no `ultra`

An unset pi or claude level means `medium`, which is what those backends are actually sent, so a
bare `/reviewerModel` reports `medium` rather than "not overridden". Levels are additionally per-MODEL, so one the
current model does not support may still fail the call. Effort applies to the commit audit
and question answers only: the brief stays low (its reply is discarded) and the stop supervisor
keeps its own. A timeout still BLOCKS the commit (the gate is fail-closed) -- raising it only
buys the reviewer more time on a large diff.
