# Backlog

## Add a Pi backend to the native Codex package

Deferred until after the current release.

- Allow a Codex developer session to use Pi for commit review and `stopContinue` supervision.
- Add Pi backend selection, executable/model/reasoning configuration, separate role sessions, failure handling, documentation, and tests.
- Preserve Pi's read-only execution boundary and do not silently fall back to Codex.
- Do not claim support for `AskUserQuestion` in Codex unless the host provides an equivalent interface.

## Watchdog tick: presence-check the teardown marker without a Read "error"

The `stopContinueWatchdog` tick uses the `Read` tool on the session's `.teardown_ready` marker, which is *normally absent*. On every healthy tick this surfaces an `Error: File does not exist` line (and occasionally a fuzzy `Did you mean …disarm?` sibling suggestion), so a correctly-running tick reads like a fault, and the logic leans on the model to distinguish a merely-missing file from a genuine I/O error.

- Replace the Read-of-an-absent-file presence check with one that does not surface as an error (e.g. a check that reports present / absent / unreadable distinctly), so healthy ticks stay quiet and a real read failure is still fail-safe (never deletes the cron).
- Keep the tick a pure presence check with all timing in Python (unchanged design); this is a tick-output/robustness fix only.
- See `docs/plans/watchdog_tick_visibility.md` and `docs/plans/watchdog_tick_batched_ls_robustness.md` for the prior related work; the `.disarm` → `.teardown_ready` staging in `reviewer_state.py` / `stop_hook.py` is correct and out of scope here.
