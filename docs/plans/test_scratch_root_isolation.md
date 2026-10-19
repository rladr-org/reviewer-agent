# Keep tests out of the real reviewer scratch root

Linux acceptance testing on 2026-10-18 found `sid-A`, `sid-EF`, `preexisting-sid`
and `fake-session-0001` in the operator's real `$TMPDIR/reviewer-agent-scratch`,
timestamped at the plugin update's test run. `commit_scratch_root()` honours
`REVIEWER_SCRATCH_ROOT`, but `tests/conftest.py` never sets it; only
`test_scratch_cleanup.py`'s `scratch_env` and two `test_commit_hook.py` tests do.
Every other commit-gate test that audits (and so calls
`commit_scratch_dir(sid, create=True)`) writes into the real root, and the
session-start hook tests run `sweep_orphan_scratch_dirs()` against it.

Consequences: test dirs mix with real review data on every machine that runs the
suite (including install/update); they outlive an uninstall because only the
plugin's own age-based sweep removes them; and a test dir could in principle share
a name with a real session's dir, which a test's rotation would then delete.

## Fix

Add an autouse fixture to `tests/conftest.py` that points `REVIEWER_SCRATCH_ROOT`
at a dir inside each test's own `tmp_path` via `monkeypatch.setenv` (created lazily
by the code under test, as in production), so both in-process
calls and hook subprocesses (which copy `os.environ`) inherit it. Tests that set
their own root keep working: their `setenv` runs after the autouse fixture and
wins. Subprocess tests that build an env from scratch (`test_host_versions`,
`test_hook_launcher`) never reach scratch code and need no change.

The Codex plugin (`plugins/reviewer-agent-codex`) has no scratch-root code, so its
conftest has no equivalent gap.

## Commit and TDD sequence

1. Commit this plan.
2. Add a regression in `tests/test_scratch_cleanup.py` asserting that, with no
   per-test setup, `commit_scratch_root()` lies under the test's own `tmp_path` and
   is exported in `os.environ`. Run it red; commit.
3. Add the conftest fixture; run the regression plus `test_scratch_cleanup.py`,
   `test_commit_hook.py` and `test_session_start_hook.py` green; confirm a run of
   those files leaves nothing new in the real scratch root; commit.

## Verification

- Plan commit: `abb7002`; red test commit: `fdfa2d7` (1 failed: the root resolved to
  the real `$TMPDIR/reviewer-agent-scratch`).
- Reproduced on macOS before the fix: `test_commit_hook.py` + `test_session_start_hook.py`
  left 12 empty dirs in the real root (`sid-A`..`sid-H`, `sid-1`, `sid-EF`,
  `preexisting-sid`, `fake-session-0001`); removed by name afterwards.
- After the fix, `test_scratch_cleanup.py`, `test_commit_hook.py` and
  `test_session_start_hook.py` passed (91 tests) and added nothing to the real root.
