---
name: stop-continue
description: Toggle or report stop-continue supervision for the current Git repository. Use when the user asks to enable or disable stop supervision or check its status.
---

# Stop Continue

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation with `on`, `off`, or
`status`, then ends the turn without a model request. On first use, plain `on` refuses to enable
and tells the operator to read `DISCLAIMER.md` and invoke the exact acceptance form:

```text
$reviewer-agent-codex:stop-continue on acceptRisk
```

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run the toggle script or emulate stop supervision
manually.
Never add `acceptRisk` for the operator. Only the trusted exact user prompt may record acceptance.
The stop supervisor is independent of the commit reviewer.

The Codex reading delay has been removed. Completion reviews start immediately; old delay
configuration/environment values are ignored. The trusted hook catches legacy exact
`$reviewer-agent-codex:stop-continue delay` commands and reports removal without changing settings.
If one reaches the model, explain that the delay is removed; do not emulate it or edit settings.

Currently, in Codex, you must press Escape to interrupt a running completion review.
Typing or submitting a reply does not interrupt the check; Codex may queue the reply until
the check finishes.
