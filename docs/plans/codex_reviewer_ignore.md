# Codex reviewer ignore parity

## Scope and behavior

Port the Claude Code host's `reviewerAgent-ignore.md` support into the independent
Codex plugin. Keep its runtime standard-library-only and Python 3.9 compatible.
The two distributions must not import one another; duplicate the small parser and
matcher with provenance comments.

- Read the optional repository-root file from Git objects, never the working tree.
  Use the index when unchanged and HEAD when staged; validate the staged candidate
  even though new rules take effect only after that commit lands. Absence is normal.
- Preserve the existing fenced `reviewer-ignore` pattern syntax and root-file
  self-exemption. Malformed patterns and invalid UTF-8 block before a reviewer runs.
- Include effective prose and skim instructions on every commit review, including
  resumed reviews. Matched paths still receive intent/schema/location review, and
  the reviewer can fetch any suspicious diff. This never skips the commit gate.
- Keep every path in the retry inventory and fingerprint. Suppress matched paths'
  inlined retry deltas before fetching them, with an explicit skim label instead.
- Surface matched file and added/deleted line counts in the tool result and retained
  review record, using Codex's existing history rather than adding a second log.
- Document the file format and delayed application in the Codex README. Leave stop
  supervision and the Claude Code implementation unchanged.

## Commit and validation sequence

1. Commit this plan.
2. Add Codex-only parser/matcher and commit integration regressions; run them and
   establish red, then commit the tests without production changes.
3. Implement the behavior, run the focused tests and full canonical Codex suite to
   green, then commit the implementation and documentation.
4. Run the canonical Codex suite again after the implementation commit:
   `cd plugins/reviewer-agent-codex && python3 -m pytest -p no:cacheprovider`.

Tests cover missing/untracked files, staged introduction/change/deletion, unstaged
edits, supported and malformed patterns, self-exemption, invalid encoding, prompt
refresh on retries, excluded delta fetching, audit counts, and approval still
committing the complete staged set. No tests call a real reviewer backend.
