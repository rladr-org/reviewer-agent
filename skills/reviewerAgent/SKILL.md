---
name: reviewerAgent
description: Toggle the reviewer agent on/off for the current project (it gates git commits and answers unanswered AskUserQuestion prompts, via the configured backend - codex by default, or pi or claude). Invoke as /reviewerAgent on or /reviewerAgent off.
argument-hint: on | off
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---
Toggling the reviewer agent for this project (`$ARGUMENTS`).

The block below is the live output of the toggle script (it runs before you see this):

!`python3 "${CLAUDE_PLUGIN_ROOT}/toggle.py" $ARGUMENTS`

Report the resulting state in one line, read from the output above:
- `reviewerAgent: ON ...` — the reviewer now gates git commits for this project and answers any
  AskUserQuestion the operator leaves unanswered, until `/reviewerAgent off`. Each exchange is
  echoed and logged to the reviewer log
  (`~/.claude/reviewer-agent/logs/reviewer.log`, or `$REVIEWER_STATE_DIR/logs/reviewer.log` if set).
  Commit review and question answering both use the configured backend. If `stopContinue` is
  enabled separately, it uses that backend too.
- `reviewerAgent: OFF ...` — the per-project flag was removed; the hooks no longer consult a
  reviewer here.
- `reviewerAgent: ERROR ...` — NOT enabled; surface the error verbatim (cwd is not a git repo, or
  `./reviewerAgent.md` is missing).

`on` also prints an `askUserQuestionTimeout ...` line (or a `WARNING - askUserQuestionTimeout NOT
armed ...`): that user setting is what makes Claude auto-close an unanswered question, which is the
only thing the answer hook reacts to. Relay it too — on the warning, the reviewer is enabled but
will not answer questions.

Note: `on` is instant — it only validates locally. Codex is normally briefed at SessionStart and
Pi is briefed lazily on the first action for each role, so the first Pi action may take longer.
