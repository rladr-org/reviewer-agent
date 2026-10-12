# Warn at session start when the plugin runs from a temporary directory

## Why

On 2026-10-07 the installed build ran from a Directory marketplace inside a session scratchpad
under `/private/tmp`. Claude Code runs a Directory-source plugin's hooks from that directory.
macOS's daily cleanup deleted the files, and every hook then exited 127, which Claude Code
treats as non-blocking. Stop supervision and the watchdog went silent and the commit gate failed
open; two commits in another project landed unreviewed. The files lived for days before the
cleanup, so a warning at session start would have caught it in time.

## Change

`session_start_hook.py` checks its own resolved directory, the plugin root. If the root lies
under a temporary directory, the hook adds a `systemMessage` to its SessionStart output, which
Claude Code shows to the user. The message names the path, says the OS can delete it and that
every hook, the commit gate included, then fails open, and points to `AGENT_UPDATE.md`. The same
fact goes to `reviewer.log`.

- **Temporary directories:** `tempfile.gettempdir()`, `/tmp`, `/var/tmp`, and macOS's per-user
  `/var/folders`, each compared after `realpath`, so `/tmp` and `/private/tmp` both match.
- The check runs on every session start, whatever the project's reviewer state: the gate is
  installed per user, not per project.
- The warning travels in the same JSON object as the watchdog's `additionalContext`, when both
  apply. Like everything else here, it never blocks the session, and an error in it is logged
  and ignored.

## Tests (red first)

- `tests/test_temp_plugin_root_warning.py`:
  - pure decision: a root under a given temp dir warns; a sibling path with a shared prefix
    (`/tmpfoo`) does not; `/tmp` and `/private/tmp` resolve alike where that symlink exists;
  - hook level: a copy of the hook files in `tmp_path` (a temp directory on macOS and Linux)
    prints a `systemMessage` naming the path. The repository's own hook does not, a case that
    is skipped when the checkout itself sits in a temp directory.
- Two watchdog tests asserted an empty stdout to mean "no re-arm context". The red commit will change
  them to assert that no `hookSpecificOutput` is present, so they also hold when the suite runs from a temp copy, as
  the reviewer's sandbox does.
