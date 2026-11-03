---
name: stopContinueDirective
description: Record a standing rule for the stopContinue supervisor in this project - when the agent may stop or must keep working - injected into every stop review and never into commit audits. Usage: /stopContinueDirective [<text>|list|remove <n>|clear]
argument-hint: <text> | list | remove <n> | clear
disable-model-invocation: true
---

<<reviewer-agent stop directive>> $ARGUMENTS
