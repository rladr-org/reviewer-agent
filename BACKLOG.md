# Backlog

## Hook launcher: treat a python3 older than 3.9 like a missing one

`hooks/run_hook.sh` runs the first `python3` on the PATH Claude Code was started with, so an
activated venv or conda environment (or none) decides the hooks' interpreter. It checks only that
`python3` exists, not its version. The install and update guides now check it once, in the
restarted target session, but a later start under another environment can switch it silently.

- In the launcher, skip a candidate whose `sys.version_info < (3, 9)` and fall through to the
  existing fail-closed path (block commits, tell the operator) when none qualifies.
- Keep the check in the launcher, not in the Python scripts: code using newer syntax would fail
  to compile before an in-script guard ran. Cost: one extra interpreter start per hook call.
- Evidence (2026-10-19, Ubuntu 20.04, release `efa0d50`): system `/usr/bin/python3` is 3.8.10;
  the smoke subset under it passed 308 of 309, the one failure being the deliberate
  `test_host_versions.py` floor check. Older interpreters were not tested.

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
