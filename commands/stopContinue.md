---
description: Toggle stopContinue completion supervision for this project. It may direct the agent to continue unfinished work already authorized by the operator. Separate from /reviewerAgent. Usage: /stopContinue on|off
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/stopcontinue_toggle.py" $ARGUMENTS`

The block above is the live output of the toggle script (it ran before you saw this).

Relay that result to the user in one short line, quoting the state verbatim from the output:
`stopContinue: ON ...`, `stopContinue: OFF ...`, or `stopContinue: ERROR ...` (surface any error
text exactly). If the output carries a second `stopContinue: WARNING ...` line, relay that warning
verbatim. Add no other commentary and take no further action.
