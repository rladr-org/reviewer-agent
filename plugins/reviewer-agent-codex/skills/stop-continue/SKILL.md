---
name: stop-continue
description: Toggle or report stop-continue supervision for the current Git repository, or configure its reading delay. Use when the user asks to enable or disable stop supervision, check its status, or view or change the delay.
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

The same trusted hook handles these exact delay commands without a model request:

```text
$reviewer-agent-codex:stop-continue delay
$reviewer-agent-codex:stop-continue delay 30
$reviewer-agent-codex:stop-continue delay clear
```

`delay` (or `delay status`) reports the effective value and source. An integer from 0 through
300 saves `stopContinueDelaySecs` in the plugin's `config.json`; 0 disables the delay. `clear`
removes that key, restoring the 60-second default unless the environment overrides it.
`REVIEWER_STOPCONTINUE_DELAY_SECS` takes precedence. This setting applies across repositories
and does not enable supervision. The hook result reports any override.

Submitting an operator message cancels a pending stop review once the host delivers
`UserPromptSubmit`. Unsent typing is not exposed to the plugin. Escape also cancels the check.
