---
description: Toggle reviewerAgent for this project. Commit audits and timed-out questions use the configured codex, pi or claude backend; stopContinue is enabled separately and uses the same backend. Usage: /reviewerAgent on|off
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/toggle.py" $ARGUMENTS`

The block above is the live output of the toggle script (it ran before you saw this).

Relay that result to the user in one short line, quoting the state verbatim from the output:
`reviewerAgent: ON ...`, `reviewerAgent: OFF ...`, or `reviewerAgent: ERROR ...` (surface any
error text exactly -- on ERROR the reviewer was NOT enabled).

`on` also arms `askUserQuestionTimeout` in the user settings, which is what makes unanswered
questions get answered. If the output carries a second `askUserQuestionTimeout ...` line, add it;
if it carries `WARNING - askUserQuestionTimeout NOT armed`, relay that warning verbatim -- the
reviewer is enabled but will not answer questions. If the output carries a
`reviewerAgent: USE AT YOUR OWN RISK ...` line, relay it verbatim. Add no other commentary and
take no further action.
