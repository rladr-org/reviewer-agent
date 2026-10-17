---
name: stopContinue
description: Toggle stopContinue completion supervision for this project, or set how long a stop waits before its review (the reading delay, default 60 seconds). It may direct the agent to continue unfinished work already authorized by the operator. Separate from /reviewerAgent. Usage: /stopContinue on [acceptRisk]|off|delay [<0..300>|status|clear]
argument-hint: on [acceptRisk] | off | delay [<0..300>|status|clear]
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" stopcontinue "$ARGUMENTS"`

The block above is the live output of the toggle script (it ran before you saw this).

Relay that result to the user in one short line, quoting the state verbatim from the output:
`stopContinue: ON ...`, `stopContinue: OFF ...`, `stopContinue: delay ...`, or
`stopContinue: ERROR ...` (surface any error text exactly). If the output carries a second
`stopContinue: WARNING ...` line, relay that warning verbatim. Add no other commentary and take no
further action.

On first use, plain `on` instead instructs the operator to read `DISCLAIMER.md` and invoke
`/stopContinue on acceptRisk`. Relay that instruction verbatim. Never add `acceptRisk` for the
operator or invoke the source script yourself.
