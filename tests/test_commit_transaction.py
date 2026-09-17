# SPDX-License-Identifier: MPL-2.0
"""Publishing exactly the reviewed snapshot, and nothing else.

These are the tests that matter: the parser decides what may be asked for, but this is where
the guarantee lives. Real Git throughout -- only a model verdict is ever stubbed elsewhere, and
stubbing Git here would test nothing, since the integrity being claimed IS Git's behaviour.

The properties pinned here, from docs/plans/claude_commit_transaction_design.md:

  * Identity is the TREE and the PARENT, not the list of staged paths. Editing a staged file
    changes neither the filenames nor the count, and it must still be caught.
  * Nothing public moves until publication. A candidate is built against private state, so a
    rejected one leaves no trace.
  * Publication is conditional on the live state still matching, checked while the locks that
    would let someone else move are held.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import reviewer_commit_txn as txn

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _git(repo, *args, **kwargs):
    # Observation has to be immune to the very variables some of these tests set: with GIT_DIR
    # exported, `git -C <repo>` reports the OTHER repository, so an unscrubbed helper would
    # measure the wrong thing and quietly agree with a broken implementation.
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith("GIT_")}
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args), capture_output=True, text=True,
        env=environment, **kwargs
    ).stdout.strip()


def _repo(tmp_path, name="repo", initial=True):
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    if initial:
        (root / "base.py").write_text("base = 0\n")
        _git(root, "add", "base.py")
        _git(root, "commit", "-q", "-m", "init")
    return root


def _stage(root, name, text):
    (root / name).write_text(text)
    _git(root, "add", name)


# --------------------------------------------------------------------------
# Capture
# --------------------------------------------------------------------------
def test_capture_records_the_tree_and_the_parent(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    head = _git(root, "rev-parse", "HEAD")
    with txn.CommitTransaction(root) as transaction:
        assert transaction.parent == head
        assert transaction.tree  # the reviewed tree, not a file list
        assert transaction.matches_live()


def test_an_edit_to_an_already_staged_file_is_detected(tmp_path):
    # The filenames and their count are unchanged, so a comparison of staged PATHS would see
    # nothing. This is the case the tree comparison exists for.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        assert transaction.matches_live()
        _stage(root, "good.py", "value = 999\n")
        assert not transaction.matches_live()


def test_staging_another_file_after_capture_is_detected(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        _stage(root, "evil.py", "unreviewed = True\n")
        assert not transaction.matches_live()


def test_head_moving_after_capture_is_detected(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        _git(root, "commit", "-q", "-m", "someone else")
        assert not transaction.matches_live()


# --------------------------------------------------------------------------
# The candidate is private
# --------------------------------------------------------------------------
def test_a_candidate_does_not_move_the_real_branch(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        assert candidate
        assert _git(root, "rev-parse", "HEAD") == before, "the public branch moved early"


def test_the_candidate_carries_the_reviewed_tree_and_parent(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        headers = _git(root, "cat-file", "-p", candidate).split("\n\n", 1)[0].splitlines()
        assert ["tree " + transaction.tree] == [h for h in headers if h.startswith("tree ")]
        assert ["parent " + transaction.parent] == [h for h in headers if h.startswith("parent ")]


# --------------------------------------------------------------------------
# Publication
# --------------------------------------------------------------------------
def test_publishing_moves_the_branch_to_the_reviewed_commit(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        transaction.publish(candidate, "reviewed message", None)
    assert _git(root, "rev-parse", "HEAD") == candidate
    assert _git(root, "log", "-1", "--format=%s") == "reviewed message"
    assert _git(root, "show", "--name-only", "--format=", "HEAD").split() == ["good.py"]


def test_publishing_refuses_when_the_staged_set_changed_after_the_candidate(tmp_path):
    # The window the whole design exists to close: approved, candidate built, then someone
    # stages something before publication.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        _stage(root, "evil.py", "unreviewed = True\n")
        staged_before_refusal = _git(root, "ls-files", "--stage")
        with pytest.raises(txn.CommitError):
            transaction.publish(candidate, "reviewed message", None)
    assert _git(root, "rev-parse", "HEAD") == before, "a commit was published anyway"
    # The INDEX has to survive, not merely the files: an implementation that reset or stashed to
    # clear its way would leave the worktree looking fine while destroying the staged work.
    assert _git(root, "ls-files", "--stage") == staged_before_refusal, \
        "the person's staged entries were altered by the refusal"
    assert sorted(_git(root, "diff", "--cached", "--name-only").split()) == \
        ["evil.py", "good.py"]
    assert (root / "evil.py").read_text() == "unreviewed = True\n"


def test_publishing_refuses_when_the_branch_advanced(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        _stage(root, "other.py", "other = 1\n")
        _git(root, "commit", "-q", "-m", "someone else")
        moved = _git(root, "rev-parse", "HEAD")
        with pytest.raises(txn.CommitError):
            transaction.publish(candidate, "reviewed message", None)
    assert _git(root, "rev-parse", "HEAD") == moved, "the other commit was clobbered"


# --------------------------------------------------------------------------
# Shapes that cannot be modelled are refused before anything happens
# --------------------------------------------------------------------------
def test_an_unborn_branch_is_supported(tmp_path):
    root = _repo(tmp_path, name="unborn", initial=False)
    _stage(root, "first.py", "first = 1\n")
    with txn.CommitTransaction(root) as transaction:
        assert transaction.parent == ""
        candidate, _ = transaction.candidate("first commit", [], None)
        transaction.publish(candidate, "first commit", None)
    assert _git(root, "rev-parse", "HEAD") == candidate
    assert _git(root, "rev-list", "--count", "HEAD") == "1"


def test_an_in_progress_merge_is_refused(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    (root / ".git" / "MERGE_HEAD").write_text(_git(root, "rev-parse", "HEAD") + "\n")
    with pytest.raises(txn.CommitError):
        with txn.CommitTransaction(root):
            pass


def test_a_hook_that_rewrites_content_blocks_the_candidate(tmp_path):
    # A pre-commit hook may change what would be committed. The candidate's tree then differs
    # from the reviewed one, so it must not become a public commit.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'extra = 1' >> good.py\ngit add good.py\n")
    hook.chmod(0o755)
    before = _git(root, "rev-parse", "HEAD")
    with txn.CommitTransaction(root) as transaction:
        with pytest.raises(txn.CommitError):
            transaction.candidate("reviewed message", [], None)
    assert _git(root, "rev-parse", "HEAD") == before, "the rewritten content was published"


def test_the_signoff_option_is_carried(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", ["--signoff"], None)
        transaction.publish(candidate, "reviewed message", None)
    assert "Signed-off-by:" in _git(root, "log", "-1", "--format=%B")


# --------------------------------------------------------------------------
# The transaction acts on the root it was given, whatever the caller's environment says
# --------------------------------------------------------------------------
def test_inherited_git_variables_cannot_redirect_the_transaction(tmp_path, monkeypatch):
    # The gate runs as a hook, and a hook can be invoked from inside another Git process, which
    # exports GIT_DIR, GIT_INDEX_FILE and friends. Inheriting those would make the transaction
    # inspect or publish a repository other than the explicit root.
    root = _repo(tmp_path, name="target")
    other = _repo(tmp_path, name="elsewhere")
    _stage(root, "good.py", "value = 1\n")
    other_head_before = _git(other, "rev-parse", "HEAD")

    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(other / ".git" / "objects"))

    with txn.CommitTransaction(root) as transaction:
        assert transaction.parent == _git(root, "rev-parse", "HEAD")
        candidate, _ = transaction.candidate("reviewed message", [], None)
        transaction.publish(candidate, "reviewed message", None)

    assert _git(root, "rev-parse", "HEAD") == candidate
    assert _git(root, "show", "--name-only", "--format=", "HEAD").split() == ["good.py"]
    assert _git(other, "rev-parse", "HEAD") == other_head_before, "another repository was moved"


def test_an_environment_supplied_identity_is_preserved(tmp_path, monkeypatch):
    # Only ROUTING variables are scrubbed. Identity through the environment is how many CI
    # systems supply the committer, and a commit the gate performs has to mean the same thing
    # as the commit the agent asked for -- dropping these would quietly change authorship, and
    # would fail outright where identity is supplied no other way.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Somebody Else")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "somebody@elsewhere")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        transaction.publish(candidate, "reviewed message", None)
    assert _git(root, "log", "-1", "--format=%an") == "Somebody Else"
    assert _git(root, "log", "-1", "--format=%ae") == "somebody@elsewhere"


# --------------------------------------------------------------------------
# The index lock is this attempt's alone, and never outlives it
# --------------------------------------------------------------------------
def test_a_refused_publication_releases_the_index_lock(tmp_path):
    # A lock left behind blocks every later Git command in the person's repository.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        _stage(root, "evil.py", "unreviewed = True\n")
        with pytest.raises(txn.CommitError):
            transaction.publish(candidate, "reviewed message", None)
    assert not (root / ".git" / "index.lock").exists(), "a stale index lock was left behind"
    # And the repository is still usable.
    _git(root, "status", "--short")
    assert _git(root, "rev-parse", "--verify", "HEAD")


def test_a_successful_publication_releases_the_index_lock(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        transaction.publish(candidate, "reviewed message", None)
    assert not (root / ".git" / "index.lock").exists()


def test_an_index_lock_held_by_someone_else_is_never_taken_or_removed(tmp_path):
    # Another Git process owns it. The transaction must refuse rather than barge in, and must
    # leave that lock exactly where it found it.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        lock = root / ".git" / "index.lock"
        lock.write_text("owned by another process\n")
        with pytest.raises(txn.CommitError):
            transaction.publish(candidate, "reviewed message", None)
        assert lock.exists(), "another process's lock was removed"
        assert lock.read_text() == "owned by another process\n"
        lock.unlink()


# --------------------------------------------------------------------------
# Termination during a prepared transaction
# --------------------------------------------------------------------------
_INTERRUPTIBLE = '''
import os, signal, sys, time
sys.path.insert(0, {repo!r})
import reviewer_commit_txn as txn


class Slow(txn.CommitTransaction):
    """Block with the transaction PREPARED and the index lock held.

    The FIRST call comes from _capture, long before any lock exists; interrupting there would
    prove nothing, which is exactly how an earlier version of this test passed without the
    handler being present at all. Only the second call, from publish, is the moment worth
    catching."""

    calls = 0

    def matches_live(self):
        Slow.calls += 1
        if Slow.calls < 2:
            return super().matches_live()
        sys.stdout.write("prepared\\n")
        sys.stdout.flush()
        time.sleep(120)
        return True


with Slow({root!r}) as transaction:
    candidate, _ = transaction.candidate("reviewed message", [], None)
    transaction.publish(candidate, "reviewed message", None)
'''


def _interrupted_publish(tmp_path, root, sig):
    """Start a publish, wait until it is prepared, signal it, and return the child."""
    script = tmp_path / "slow_publish.py"
    script.write_text(_INTERRUPTIBLE.format(
        repo=str(_PACKAGE_ROOT), root=str(root)
    ))
    child = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "prepared", "the child never reached publication"
    child.send_signal(sig)
    child.wait(timeout=30)
    return child


def test_a_signal_during_a_prepared_transaction_releases_the_lock(tmp_path):
    # Dying skips `finally`, so without a handler the index lock would outlive the process and
    # block every later Git command in the person's repository.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    _interrupted_publish(tmp_path, root, signal.SIGTERM)
    assert not (root / ".git" / "index.lock").exists(), "a stale index lock was left behind"
    assert _git(root, "rev-parse", "HEAD") == before, "a commit was published after termination"
    assert _git(root, "status", "--short") is not None  # the repository is still usable


def test_a_signal_during_a_prepared_transaction_publishes_nothing_later(tmp_path):
    # The transaction was prepared but never committed. Abandoning it must leave every
    # reference exactly where it was, now and afterwards.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    _interrupted_publish(tmp_path, root, signal.SIGINT)
    time.sleep(1)  # nothing may appear after the fact
    assert _git(root, "rev-parse", "HEAD") == before
    assert _git(root, "rev-list", "--count", "HEAD") == "1"
    assert sorted(_git(root, "diff", "--cached", "--name-only").split()) == ["good.py"]


def test_signal_handlers_are_restored_after_a_normal_publish(tmp_path):
    # The gate installs handlers only for the critical section; leaving them installed would
    # change how the surrounding hook responds to termination.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    original = signal.getsignal(signal.SIGTERM)
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        transaction.publish(candidate, "reviewed message", None)
    assert signal.getsignal(signal.SIGTERM) is original


_EARLY_INTERRUPTIBLE = '''
import sys, time
sys.path.insert(0, {repo!r})
import reviewer_commit_txn as txn


class SlowStart(txn.CommitTransaction):
    """Block with the index lock HELD but no transaction started yet.

    _check_in_progress runs once during capture and again at the very top of publish, after the
    lock is taken and before the update-ref child exists. That gap was outside the signal
    handler, so termination there stranded the lock."""

    calls = 0

    def _check_in_progress(self):
        SlowStart.calls += 1
        if SlowStart.calls < 2:
            return super()._check_in_progress()
        sys.stdout.write("locked\\n")
        sys.stdout.flush()
        time.sleep(120)


with SlowStart({root!r}) as transaction:
    candidate, _ = transaction.candidate("reviewed message", [], None)
    transaction.publish(candidate, "reviewed message", None)
'''


def test_a_signal_before_the_transaction_starts_still_releases_the_lock(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    script = tmp_path / "early_publish.py"
    script.write_text(_EARLY_INTERRUPTIBLE.format(repo=str(_PACKAGE_ROOT), root=str(root)))
    child = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "locked"
    assert (root / ".git" / "index.lock").exists(), "the premise failed: no lock was held"
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=30)
    assert not (root / ".git" / "index.lock").exists(), "a stale index lock was left behind"
    assert _git(root, "rev-parse", "HEAD") == before


# --------------------------------------------------------------------------
# After the reference has moved, the outcome is "committed", never "nothing happened"
# --------------------------------------------------------------------------
def test_publish_returns_the_published_commit(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    with txn.CommitTransaction(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        assert transaction.publish(candidate, "reviewed message", None) == candidate


def test_a_failure_after_publication_reports_the_commit_rather_than_a_failed_commit(tmp_path):
    # The reference has already moved. Reporting this as an ordinary failure would tell the
    # agent nothing was committed, and the natural response to that is to commit again.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")

    class Broken(txn.CommitTransaction):
        def _after_commit(self, process):
            raise RuntimeError("bookkeeping blew up")

    with Broken(root) as transaction:
        candidate, _ = transaction.candidate("reviewed message", [], None)
        with pytest.raises(txn.CommitPublished) as raised:
            transaction.publish(candidate, "reviewed message", None)
        assert raised.value.commit == candidate

    assert _git(root, "rev-parse", "HEAD") == candidate, "the commit really did land"
    assert not (root / ".git" / "index.lock").exists()


def test_commit_published_is_not_mistaken_for_an_ordinary_failure():
    # It subclasses CommitError so existing handling still catches it, but a caller that cares
    # about duplicates can tell the two apart and must.
    assert issubclass(txn.CommitPublished, txn.CommitError)
    assert not issubclass(txn.CommitCancelled, txn.CommitPublished)


_POST_PUBLICATION = '''
import sys, time
sys.path.insert(0, {repo!r})
import reviewer_commit_txn as txn


class SlowFinish(txn.CommitTransaction):
    """Block AFTER the reference has already moved.

    _after_commit runs once the transaction is committed, so this is the window where a real
    commit exists but the caller has not reported it."""

    def _after_commit(self, process):
        sys.stdout.write("published\\n")
        sys.stdout.flush()
        time.sleep(120)


with SlowFinish({root!r}) as transaction:
    candidate, _ = transaction.candidate("reviewed message", [], None)
    sys.stderr.write("candidate " + candidate + "\\n")
    sys.stderr.flush()
    transaction.publish(candidate, "reviewed message", None)
'''


def test_a_signal_after_publication_leaves_the_commit_and_no_stale_lock(tmp_path):
    # The reference has moved. Termination here must not strand the lock, and must not undo or
    # duplicate the commit. The index is left clean, which is itself what stops a retry from
    # producing a second commit.
    root = _repo(tmp_path)
    _stage(root, "good.py", "value = 1\n")
    before = _git(root, "rev-parse", "HEAD")
    script = tmp_path / "post_publication.py"
    script.write_text(_POST_PUBLICATION.format(repo=str(_PACKAGE_ROOT), root=str(root)))
    child = subprocess.Popen(
        [sys.executable, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert child.stdout.readline().strip() == "published", "the child never reached publication"
    assert (root / ".git" / "index.lock").exists(), "the premise failed: no lock was held"
    child.send_signal(signal.SIGTERM)
    child.wait(timeout=30)
    time.sleep(0.5)

    assert not (root / ".git" / "index.lock").exists(), "a stale index lock was left behind"
    published = _git(root, "rev-parse", "HEAD")
    assert published != before, "the commit was lost even though the reference had moved"
    assert _git(root, "log", "-1", "--format=%s") == "reviewed message"
    assert _git(root, "rev-list", "--count", "HEAD") == "2"
    # Nothing is staged any more, so an agent that retries meets the nothing-staged refusal
    # rather than producing a duplicate.
    assert _git(root, "diff", "--cached", "--name-only") == ""


@pytest.mark.skipif(not hasattr(signal, "pthread_sigmask"),
                    reason="signal blocking is unavailable on this platform")
def test_blocked_signals_defers_delivery_rather_than_dropping_it():
    # The indivisible pairs rely on this primitive, so it is worth proving rather than
    # assuming: inside the block the signal must not be delivered, and afterwards it must be.
    delivered = []
    previous = signal.signal(signal.SIGTERM, lambda number, frame: delivered.append(number))
    try:
        with txn._blocked_signals():
            os.kill(os.getpid(), signal.SIGTERM)
            assert delivered == [], "the signal was delivered inside the blocked section"
        for _ in range(1000):  # delivery happens at the next interpreter check
            if delivered:
                break
        assert delivered == [signal.SIGTERM], "the signal was dropped instead of deferred"
    finally:
        signal.signal(signal.SIGTERM, previous)


# --------------------------------------------------------------------------
# The captured patch handed to a patch-only reviewer (the claude backend)
# --------------------------------------------------------------------------
def test_patch_returns_the_captured_staged_diff(tmp_path):
    root = _repo(tmp_path)
    _stage(root, "f.py", "x = 1\n")
    with txn.CommitTransaction(root) as transaction:
        text, truncated = transaction.patch(1_000_000)
    assert not truncated
    assert "f.py" in text
    assert "x = 1" in text


def test_patch_is_the_captured_snapshot_not_the_live_index(tmp_path):
    # The point of handing over the CAPTURED diff: a stage that lands after capture is not in it,
    # so the reviewer cannot approve content that publication will then refuse.
    root = _repo(tmp_path)
    _stage(root, "f.py", "x = 1\n")
    with txn.CommitTransaction(root) as transaction:
        _stage(root, "late.py", "late = 1\n")  # the live index moves after capture
        text, _ = transaction.patch(1_000_000)
    assert "f.py" in text
    assert "late.py" not in text


def test_patch_reports_truncation_past_the_limit(tmp_path):
    # `truncated` is load-bearing: the caller must refuse a verdict formed on a partial patch.
    root = _repo(tmp_path)
    _stage(root, "big.py", "v = 1\n" * 1000)
    with txn.CommitTransaction(root) as transaction:
        text, truncated = transaction.patch(200)
    assert truncated
    assert len(text.encode("utf-8", "replace")) <= 200


def test_patch_fails_closed_when_the_diff_cannot_run(tmp_path, monkeypatch):
    # A diff that times out or a git that will not launch must become the handled empty-patch
    # (fail-closed) result, not an exception escaping the hook -- exactly as staged_patch does.
    root = _repo(tmp_path)
    _stage(root, "f.py", "x = 1\n")
    with txn.CommitTransaction(root) as transaction:
        def timeout(*a, **k):
            raise subprocess.TimeoutExpired(cmd="git diff", timeout=60)
        monkeypatch.setattr(txn.subprocess, "run", timeout)
        assert transaction.patch(1_000_000) == ("", False)

        def missing(*a, **k):
            raise OSError("git not found")
        monkeypatch.setattr(txn.subprocess, "run", missing)
        assert transaction.patch(1_000_000) == ("", False)


def test_patch_ignores_a_repository_external_diff_and_textconv(tmp_path):
    # A repo's diff.external / textconv must not be able to show the reviewer a FORGED patch while
    # the real captured tree is what gets committed -- that would approve code the reviewer never
    # saw. --no-ext-diff --no-textconv defeats it.
    root = _repo(tmp_path)
    forger = root / "forge.sh"
    forger.write_text("#!/bin/sh\necho 'FORGED-the-reviewer-must-not-see-this'\n")
    forger.chmod(0o755)
    _git(root, "config", "diff.external", str(forger))
    _stage(root, "f.py", "real_change = 1\n")
    with txn.CommitTransaction(root) as transaction:
        text, _ = transaction.patch(1_000_000)
    assert "FORGED" not in text
    assert "real_change = 1" in text
