---
name: reviewer-agent
description: Toggle or report the reviewer-agent commit gate for the current Git repository. Use when the user asks to enable, disable, turn on, turn off, or check the autonomous staged-commit reviewer.
---

# Reviewer Agent

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation with one action: `on`,
`off`, or `status`, then ends the turn without a model request.

```text
$reviewer-agent-codex:reviewer-agent on
```

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run the toggle script or substitute a manual reviewer.
Do not create `reviewerAgent.md` automatically: enabling must fail until the operator writes the
repository-specific review brief.
