---
name: reviewerAgent
description: Toggle the reviewer agent for the current project (it gates git commits via the configured backend - codex by default, or pi or claude). First enablement requires the operator's exact /reviewerAgent on acceptRisk invocation after reading DISCLAIMER.md.
argument-hint: on | off
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---
Toggling the reviewer agent for this project (`$ARGUMENTS`).

The block below is the live output of the toggle script (it runs before you see this):

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" agent "$ARGUMENTS"`

Report the resulting state in one line, read from the output above:
- `reviewerAgent: ON ...` — the reviewer now gates git commits for this project until
  `/reviewerAgent off`. Each review is logged to the reviewer log
  (`~/.claude/reviewer-agent/logs/reviewer.log`, or `$REVIEWER_STATE_DIR/logs/reviewer.log` if set).
  Commit review uses the configured backend. If `stopContinue` is enabled separately it uses that
  backend too, and also reroutes any `AskUserQuestion` to the stop supervisor.
- `reviewerAgent: OFF ...` — the per-project flag was removed; the hooks no longer consult a
  reviewer here.
- `reviewerAgent: ERROR ...` — NOT enabled; surface the error verbatim (cwd is not a git repo, or
  `./reviewerAgent.md` is missing).


Note: `on` is instant — it only validates locally. Codex is normally briefed at SessionStart and
Pi is briefed lazily on the first action for each role, so the first Pi action may take longer.
