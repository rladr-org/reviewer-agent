---
name: reviewerDirective
description: Record a standing instruction for reviewerAgent in this project - a fact or authorization it should treat as given, injected into every commit/question/stop review. Usage: /reviewerDirective [<text>|list|remove <n>|clear]
argument-hint: <text> | list | remove <n> | clear
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/directive_toggle.py" $ARGUMENTS`

The block above is the live output of the directive script (it ran before you saw this).

Relay that result to the user in one short line, quoting it verbatim from the output (the
directive recorded, the list, or the error). Add no commentary and take no further action --
in particular, do not retry a blocked commit off the back of this.
