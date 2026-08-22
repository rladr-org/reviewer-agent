---
description: "Show what review history this project has retained, or purge it. Usage: /reviewerHistory [clear]"
allowed-tools: Bash(python3:*)
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_history_toggle.py" $ARGUMENTS`

The block above is the live output of the history script (it ran before you saw this).

Relay that result to the user, quoting it verbatim from the output -- one short line per
`reviewerHistory:` line present. Add no commentary and take no further action.

Forms: `/reviewerHistory` reports how many reviews are retained for this project, where they
are, and what the retention cap is; `/reviewerHistory clear` deletes them.

What is retained and why it is worth purging: every completed audit's full verdict is kept, and
verdicts quote the staged diff they judged, so the directory accumulates a plaintext record of
the code under review as well as of the reviews. The cap (newest 100 per project by default,
oldest pruned first) bounds its size; `clear` is how an operator removes it without uninstalling
the plugin. `clear` removes this project's retained reviews and its saved latest rulings, and
touches no other repository and no reviewer session.
