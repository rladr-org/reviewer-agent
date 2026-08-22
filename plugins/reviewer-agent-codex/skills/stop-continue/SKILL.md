---
name: stop-continue
description: Toggle or report stop-continue supervision for the current Git repository. Use when the user asks Codex to keep working autonomously, prevent premature stops, disable stop supervision, or report whether it is enabled.
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
