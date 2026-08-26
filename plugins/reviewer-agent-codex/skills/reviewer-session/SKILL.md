---
name: reviewer-session
description: Show or rotate the commit-reviewer child session for the current Codex thread, or configure its automatic audit-count rotation. Use when the user asks which reviewer session is active, asks to rotate or reset a slow/confused reviewer, needs a fresh reviewer after repeated latency, or asks to change how many completed audits a reviewer handles.
---

# Reviewer Session

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation, then ends the control
turn without a model request. Supported forms are:

```text
$reviewer-agent-codex:reviewer-session status
$reviewer-agent-codex:reviewer-session rotate
$reviewer-agent-codex:reviewer-session limit <1..1000|off|clear>
```

The default limit is three completed non-retry audits. An audit is a retry whenever it follows a
rejection; retries never advance the limit, even after staged content changes or the reviewer is
manually rotated. A first-pass rejection is itself a completed audit and advances the counter
once. `off` disables scheduled rotation, and `clear` returns to the config-file or built-in
default. The config key is `reviewerSessionAuditLimit`, where `0` disables rotation.

Use `rotate` when the operator requests an immediate fresh reviewer. A timeout also retires its
reviewer automatically. Automatic and manual rotation affect only the commit reviewer; the
separate stop-continue supervisor is unchanged. The next commit audit starts a fresh reviewer and
sends the full standing/project brief.

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run a script or mutate reviewer state manually.
