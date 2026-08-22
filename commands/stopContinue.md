---
description: Toggle stopContinue - keeps the agent working autonomously instead of stalling to ask (push/run tests/shut down idle GPU). Separate from /reviewerAgent. Usage: /stopContinue on|off
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/stopcontinue_toggle.py" $ARGUMENTS`

The block above is the live output of the toggle script (it ran before you saw this).

Relay that result to the user in one short line, quoting the state verbatim from the output:
`stopContinue: ON ...`, `stopContinue: OFF ...`, or `stopContinue: ERROR ...` (surface any error
text exactly). Add no commentary and take no further action.
