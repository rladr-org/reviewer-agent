# SPDX-License-Identifier: MPL-2.0
import sys
from pathlib import Path

import pytest

# Make the reviewerAgent package modules importable from the tests.
# The repo keeps scripts under scripts/; the legacy single-directory install keeps them
# beside tests/. Resolve either so one test suite serves both trees.
_BASE = Path(__file__).resolve().parent.parent
SCRIPTS = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _forget_project_keys():
    # reviewer_state remembers git's project-key answers briefly; no test may see another test's.
    import reviewer_state
    reviewer_state._project_keys.clear()
    yield
    reviewer_state._project_keys.clear()


@pytest.fixture(autouse=True)
def _never_spend_a_real_reviewer_call(monkeypatch):
    """Point BOTH backend binaries at the test stub for EVERY test.

    Observed 2026-07-30: a test that monkeypatched `brief_and_capture` kept passing after
    `ensure_session` moved to `brief_and_capture_text`, and the unpatched call reached the REAL
    codex binary and started a live session. A stale seam should fail loudly, not quietly spend
    an API call. Tests that need a different binary (a missing one, a failing one) still set
    these themselves, and their monkeypatch.setenv overrides this one.

    pi is pinned for the same reason and was NOT covered until 2026-08-01: `pi_bin()` falls back
    to `shutil.which("pi") or "pi"`, so on a machine with pi installed the identical mistake
    would have run the real pi binary rather than failing. The reviewer_lib split relocates the
    pi seams (`pi_argv`, `_run`), which is exactly when a stale patch becomes possible, so the
    gap is closed before that step rather than after the first live call."""
    fake = str(Path(__file__).parent / "fake_codex.py")
    monkeypatch.setenv("REVIEWER_CODEX_BIN", fake)
    monkeypatch.setenv("REVIEWER_PI_BIN", fake)


@pytest.fixture(autouse=True)
def _private_scratch_root(tmp_path, monkeypatch):
    """Point the reviewer scratch root inside EVERY test's own tmp_path.

    Linux testing 2026-10-18 found sid-A, sid-EF, preexisting-sid and fake-session-0001 in the
    operator's real $TMPDIR/reviewer-agent-scratch: any test that audits a commit creates a scratch
    dir, and only a few set this themselves. Those dirs outlived uninstall (only the plugin's own
    sweep removes them) and could share a name with a real session's dir. setenv (not a patch) so
    hook subprocesses inherit it; tests that set their own root still win."""
    monkeypatch.setenv("REVIEWER_SCRATCH_ROOT", str(tmp_path / "reviewer-agent-scratch"))


@pytest.fixture(autouse=True)
def _no_stop_reading_delay(monkeypatch):
    """The stop review waits 60 s by default so the operator can read the reply first. No test
    may sit through that wait; tests of the delay set the variable themselves."""
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "0")


