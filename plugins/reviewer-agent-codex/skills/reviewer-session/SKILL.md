---
name: reviewer-session
description: Show or rotate the commit-reviewer child session for the current Codex thread. Use when the user asks which reviewer session is active, asks to rotate or reset a slow/confused reviewer, or needs a fresh reviewer after repeated latency.
---

# Reviewer Session

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation with one action:
`status` or `rotate`, then ends the turn without a model request.

```text
$reviewer-agent-codex:reviewer-session status
```

Use `rotate` only when the operator requests it or when the reviewer is demonstrably slow or
confused. An audit that reaches the plugin timeout retires its reviewer automatically, so another
manual rotation is unnecessary in that case. Rotation affects only the commit reviewer owned by
the current Codex thread; it does not rotate the separate stop-continue supervisor. The next
commit audit starts a fresh reviewer and sends the full standing/project brief.

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run a script or mutate reviewer state manually.
