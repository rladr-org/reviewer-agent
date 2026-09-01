---
name: reviewerSession
description: "Show or rotate this Claude session's reviewerAgent session, or configure automatic rotation. Usage: /reviewerSession [rotate | limit <N|off|clear>]"
argument-hint: rotate | limit <N|off|clear>
disable-model-invocation: true
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" session "$ARGUMENTS"`

The block above is the live output of the session script (it ran before you saw this).

Relay that result to the user, quoting it verbatim from the output -- one short line per
`reviewerSession:` line present. Add no commentary and take no further action.

Forms: `/reviewerSession` shows the current reviewer session and effective automatic limit;
`/reviewerSession rotate` retires it; `/reviewerSession limit <N>` changes the limit;
`/reviewerSession limit off` disables automatic rotation; and `/reviewerSession limit clear`
returns to the config-file or built-in default.

Automatic rotation defaults to three completed non-retry audits. Any commit attempt after a rejection is a
retry until an audit approves, and retry audits never advance the counter. The persistent config
key is `reviewerSessionAuditLimit`; `0` disables rotation. Manual rotation remains available when
you want a changed standing brief to reach a live session immediately.
A first-pass rejection counts as one completed audit; only the attempts after it are retries.
