---
name: reviewer-agent
description: Toggle or report the reviewer-agent commit gate for the current Git repository. Use when the user asks to enable, disable, turn on, turn off, or check the autonomous staged-commit reviewer.
---

# Reviewer Agent

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation with `on`, `off`, or
`status`, then ends the turn without a model request. On first use, plain `on` refuses to enable
and tells the operator to read `DISCLAIMER.md` and invoke the exact acceptance form:

```text
$reviewer-agent-codex:reviewer-agent on acceptRisk
```

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run the toggle script or substitute a manual reviewer.
Never add `acceptRisk` for the operator. Only the trusted exact user prompt may record acceptance.
Do not create `reviewerAgent.md` automatically: enabling must fail until the operator writes the
repository-specific review brief.
