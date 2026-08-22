---
name: reviewer-history
description: Show or purge the retained commit-review record for the current repository. Use when the user asks how many reviews are kept, where verdicts are stored, or asks to delete review history without uninstalling the plugin.
---

# Reviewer History

The trusted `UserPromptSubmit` hook applies an exact namespaced invocation with one action:
`status` or `clear`, then ends the turn without a model request.

```text
$reviewer-agent-codex:reviewer-history status
```

`status` reports how many reviews are retained for this repository, the directory holding them,
and the retention cap. Retention keeps the newest 100 verdicts per repository by default and
prunes oldest-first; `REVIEWER_CODEX_REVIEW_HISTORY_KEEP`, or `reviewHistoryKeep` in the plugin
config, changes the cap.

`clear` deletes this repository's retained reviews and its saved latest rulings. Use it when the
operator asks to remove the review record: verdicts quote the staged diffs they judged, so the
directory holds a plaintext copy of reviewed code as well as of the reviews themselves. Clearing
affects one repository, removes no reviewer session, and disables nothing.

The exact invocation should never reach this skill. If it does, tell the user to start a new
plugin-enabled thread and repeat it; do not run a script or delete state manually.
