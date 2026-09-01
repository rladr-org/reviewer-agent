---
name: reviewerDirective
description: Record a standing instruction for reviewerAgent in this project - a fact or authorization it should treat as given, injected into every commit/question/stop review. Usage: /reviewerDirective [<text>|list|remove <n>|clear]
argument-hint: <text> | list | remove <n> | clear
disable-model-invocation: true
---

<<reviewer-agent directive>> $ARGUMENTS
