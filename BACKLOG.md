# Backlog

## Add a Pi backend to the native Codex package

Deferred until after the current release.

- Allow a Codex developer session to use Pi for commit review and `stopContinue` supervision.
- Add Pi backend selection, executable/model/reasoning configuration, separate role sessions, failure handling, documentation, and tests.
- Preserve Pi's read-only execution boundary and do not silently fall back to Codex.
- Do not claim support for `AskUserQuestion` in Codex unless the host provides an equivalent interface.
