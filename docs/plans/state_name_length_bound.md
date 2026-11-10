# State and temporary file names stay within the 255-byte name limit

## Problem

On 2026-11-10 the Codex commit reviewer ran the root suite in its sandbox and reported 8
failures that a local run did not show. Its reviews had reported about seven such failures since
at least 2026-08-30, and they were set aside as sandbox effects. Its own pytest output (the Codex
rollout log for the review) names them:

- `test_api_drop_watch.py::test_a_starting_watcher_ends_an_orphaned_watcher_and_leaves_a_live_one`:
  the sandbox refuses to run `ps` ("execvp() of 'ps' failed: Operation not permitted"), so the
  cleanup skips itself. This is the eighth failure, already resolved: 1.4.7 skips the test
  where `ps` cannot run.
- Seven tests in `test_claude_backend.py`, `test_commit_hook.py` and `test_reviewer_session.py`
  fail with `OSError: [Errno 63] File name too long`, or with the commit gate's fail-closed
  `deny` that the same error causes.

The reviewer runs with `TMPDIR` set to its scratch directory
(`/private/var/folders/.../T/reviewer-agent-scratch/<36-character session id>/`), so every test
repository has a long path. The same suite inside the same sandbox with the default `TMPDIR`
passes: 1839 passed, 0 failed.

The cause is a real defect. `reviewer_base._atomic_write_text` (both writers, `reviewer_base.py`
lines 74 and 96) writes a sibling named `.<name>.<32 hex>.tmp`, 38 bytes longer than the target.
State names are budgeted at 238 bytes (`_MAX_STEM_BYTES` in `reviewer_state.py`: a readable part of
up to 160 bytes, `_`, a 20-character digest, and suffixes of up to 57 bytes). For a repository
whose sanitized path is longer than about 139 bytes, the temporary name exceeds 255 bytes; the
reviewer's run produced a 258-byte one. The write fails, the commit gate crashes and blocks the
commit ("reviewerAgent's commit gate crashed (OSError: [Errno 63] File name too long ..."), and
rotation and retry records are lost. `_atomic_write_text` on a valid 228-byte name reproduces it
outside any sandbox.

A per-name byte budget has now failed three times. `_watchdog_marker_stem` and
`_carryover_marker_stem` were each moved to a fixed digest after the same overflow, one file type
at a time. The budget comment is also out of date: `.sess_<uuid>.reviewer-audits.json` is a
63-byte suffix, so root names reach 244 bytes.

The Codex package builds temporary names the same way (`liveness.py:45`, `state.py:285`). Its
project stem keeps one path component (`Path(key).parent.name`, up to 255 bytes) plus a digest,
so a long repository folder name overflows its names too. Its claim names are built from
digest-named targets and stay well under the limit.

## Constraint: an update keeps every existing state file

Installed users keep their on/off flags, risk receipts, reviewer sessions, audits and review
history across the update. The changes below leave the name of each file that can exist today
unchanged, with one exception: Codex state of a repository whose own folder name (one directory
name, apart from the path leading to it) is longer than 165 bytes (change 2). For such a
repository the update leaves these existing files unread under their old names:

| File (name = stem + suffix) | Suffix | Folder names affected | Effect |
|---|---|---|---|
| feature flag | `.stop.json`, `.reviewer.json` | 166 to 186, 166 to 182 bytes | features read as off; the user turns them on again |
| risk receipt | `.risk.accepted.json` | 166 to 177 bytes | the user accepts the risk notice again |
| deferred-stop marker | `.stop.defer.json` | 166 to 180 bytes | one pending deferral of a stop review is dropped |
| review-history directory | `.history` | 166 to 226 bytes | earlier verdicts stay on disk, absent from the history the reviewer reads |

These are all the Codex names built from the project stem that can exist today: a file written
through the atomic writer exists only where the stem (folder name plus 21 bytes) plus the
suffix plus the 38-byte temporary addition fits in 255 bytes, and the directory where the stem
plus `.history` fits. The fifth such name, the last-review file (`.<64 hex>.txt`), cannot exist
for a folder name over 165 bytes.

The operator accepted the flag and receipt rename on 2026-11-10. The deferred-stop marker and
the history directory affect the same repositories and are to be confirmed with the operator
before the 1.4.8 release. The update carries no migration.

## Change

1. **Fixed-length temporary names.** Every atomic writer names its sibling `.<32 hex>.tmp`
   (37 bytes), whatever the target is called: both writers in `reviewer_base.py`, and
   `liveness.py:45` and `state.py:285` in the Codex package. Uniqueness comes from the random
   part, and no reader or sweep matches temporary names. A target of up to 255 bytes can then be
   written, which covers every root name (244 bytes at most) and fixes the seven failures.
   The first failing test for a long-path repository showed a second crash of the same kind.
   `reviewer_transcript.resolve_transcript_path` falls back to the conventional transcript
   location, whose folder is named after the whole working directory; for a long one that
   name is over the limit and asking whether it exists raises `ENAMETOOLONG`. Required
   change: the fallback catches `OSError` and answers "no transcript there".
2. **Bounded Codex project stem.** `_project_stem` (`state.py:362`) trims the readable folder name
   to 165 UTF-8 bytes, the same way the root `_state_stem` trims its readable part. 165 is
   255 minus the `-` and 20-character digest (21) and the longest suffix appended to the stem,
   `.<64 hex>.txt` (69). Every file of a project shares one stem, so the readers that match on
   the stem prefix (`state.py:1380`) and on suffixes (`state.py:485`) keep working.

   Effect on existing files: folder names of 165 bytes or less keep their names. For longer
   folder names the flags, risk receipt and review-history directory that are writable today get
   a new name, as listed in the constraint above; the last-review file of such a repository
   already fails to write today. Preserving those names would need a per-file trim length, which
   breaks the shared project prefix, or a one-time rename, which adds more risk than the case it
   covers.
3. **A size test over every path builder.** One test per package calls every function that
   returns a state path with the largest inputs it accepts (a 255-byte folder name, a deep
   project path, a UUID session id, each role and feature) and asserts every name is at most
   255 bytes. A new suffix or builder that breaks the bound fails this test. The list of builders
   is the audit; it lives in the test.

   Session ids from Claude Code and Codex are UUIDs. The root package places the sanitized id in
   the name unbounded (`reviewer_state.py:1521`); the size test fixes the assumption at UUID
   length and this plan leaves the id as it is.
4. **Long-path run.** CI adds one job per operating system (macOS and Linux, one Python version)
   that runs both suites with `TMPDIR` set to a directory whose path is at least 200 bytes, so
   every test repository has a long path. Locally:
   `TMPDIR=<long dir> python3 -m pytest tests -q`.

   Four root tests build, in their own setup, a file or folder named after the whole
   sanitized path: three legacy-state adoption tests in `test_reviewer_capsule.py` and the
   derived-transcript test in `test_operator_transcript.py`. Such a name cannot exist under a
   long temporary directory, so each skips there with that reason. Production already
   handles the same case: the legacy migration returns on `OSError`, and the transcript
   fallback now does (change 1).
5. **Clear message.** When a state write fails with `ENAMETOOLONG`, the commit gate's message says
   a state file name or path exceeds the filesystem's limit and names the file. The gate still
   blocks the commit.
6. Release as 1.4.8 once both suites pass, including the long-path run.

Left out:

- Shortening the reviewer's scratch directory: its long path is what exposed the defect.
- A helper that remaps a whole over-long file name: readers find files by the project prefix, and
  a remapped name would fall outside that match.
- Short digest-only names for every state file: that renames every existing file and needs a
  migration.

## Tests

Written first; each fails before the change, except the root size test, which passes today
(root names reach 244 bytes) and guards the bound from here on.

- `_atomic_write_text` and `_publish_if_absent` write targets of 228 and 255 bytes.
- The transcript lookup for a long working directory answers "no transcript".
- The commit gate in a repository whose sanitized path exceeds 200 bytes: an approving review
  allows the commit, and the reviewer session id and retry records are written and read back.
- Root size test: every state path builder returns a name of at most 255 bytes for the largest
  inputs.
- Codex: its two atomic writers with 255-byte targets; the project stem for a 250-byte folder
  name is at most 186 bytes, is the same on every call, differs between two long folder names
  that share their first 165 bytes, and is unchanged for a 165-byte folder name; feature flags
  for that project are written and read back; the Codex size test.
- The long-path CI jobs exist and set a `TMPDIR` of at least 200 bytes.
