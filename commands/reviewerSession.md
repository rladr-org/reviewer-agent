---
description: "Show this Claude session's reviewerAgent session, or retire it so the next gated action briefs a fresh reviewer with the current standing brief. Usage: /reviewerSession [rotate]"
allowed-tools: Bash(python3:*)
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_session_toggle.py" $ARGUMENTS`

The block above is the live output of the session script (it ran before you saw this).

Relay that result to the user, quoting it verbatim from the output -- one short line per
`reviewerSession:` line present. Add no commentary and take no further action.

Forms: `/reviewerSession` shows the current reviewer session and what rotating would do;
`/reviewerSession rotate` retires it.

Rotation is manual by design. Automatic rotation was measured and rejected: the FIRST commit
review in a session costs a median 93k tokens against a flat ~40k steady state, so rotating on a
schedule pays a cold start every time. Rotate when you want a changed standing brief
(`reviewerAgent.md`, or the plugin's own system prompt) to reach a session that is already
running -- the brief is sent once per session -- or when accumulated reviewer context is no
longer useful.
