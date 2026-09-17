#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build a commit against captured Git state and publish it with a locked compare-and-swap.

This is where the gate's guarantee lives. The reviewer judges a snapshot; this module makes sure
the commit that becomes public is that snapshot and nothing else.

Three ideas carry the whole design, and each exists because the obvious alternative is broken:

  * **Identity is the TREE and the PARENT, not the staged file list.** Editing an already-staged
    file changes neither the paths nor their count, so a comparison of filenames sees nothing.
    `write-tree` collapses content, file modes, deletions, symlinks and submodule pointers into
    one object id, which is the only thing worth comparing.
  * **The candidate is built privately.** A real `git commit` runs against a private `GIT_DIR`
    inside the repository's own metadata, so the repository's hooks and signing behave exactly
    as they normally would, while no public reference moves. A candidate whose tree or parents
    differ from the review -- a `pre-commit` hook that rewrote content, say -- is thrown away
    without ever having been visible.
  * **Publication is a compare-and-swap under locks.** Checking and then committing leaves a
    window in which anything may stage. Instead the live `index.lock` is held and a Git
    reference transaction is `prepare`d, so Git holds the reference locks too; only then is the
    live state re-checked, and only then is the transaction committed.

Standard-library only and Python 3.9 compatible, like the rest of the runtime, and it imports
nothing from this package so the module layering stays acyclic.
"""
import contextlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
from pathlib import Path

# A prepared reference transaction is what makes publication atomic. Older Git cannot do it, and
# approximating it with a check followed by an unlocked write is precisely the race this module
# exists to close, so the floor is enforced rather than worked around.
MINIMUM_GIT = (2, 48)
_VERSION_RE = re.compile(r"(\d+)\.(\d+)")
# Git states whose parent and message semantics are not an ordinary commit's. Replacing them
# silently would publish something other than what the person is in the middle of doing.
_IN_PROGRESS = (
    "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer",
)


# Variables that decide WHICH repository, index or object store a Git command acts on. The gate
# runs as a hook, and a hook can be invoked from inside another Git process, which exports these;
# inheriting them would point a live-repository command at a different repository from the
# explicit root, the same class of fail-open as an unresolvable `-C` target and harder to spot.
_ROUTING_VARIABLES = (
    "GIT_DIR", "GIT_COMMON_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM", "GIT_PREFIX",
)


def _clean_environment():
    """The caller's environment with only the repository-ROUTING variables removed.

    Deliberately not every `GIT_*`: identity and configuration variables such as
    `GIT_AUTHOR_NAME`, `GIT_COMMITTER_EMAIL` and `GIT_AUTHOR_DATE` are how a great many CI
    systems supply the committer, and a commit the gate performs must mean the same thing as
    the commit the agent asked for. Dropping them would quietly change authorship, and would
    fail outright where identity is supplied only through the environment.

    What is removed is exactly what would send the command to the wrong repository."""
    return {name: value for name, value in os.environ.items()
            if name not in _ROUTING_VARIABLES}


# Termination signals worth reacting to. A signal delivered to this process does NOT reach the
# `update-ref` child just because they share a process group, and dying skips `finally`, so
# without these the prepared transaction and the index lock would both be left behind.
_ABORT_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")


@contextlib.contextmanager
def _blocked_signals():
    """Defer catchable termination until this block finishes.

    Some pairs of statements have to happen together or not at all: taking the index lock and
    recording that we own it, and publishing the reference and recording that we did. A signal
    landing between either pair leaves the process believing the opposite of the truth -- a lock
    it will not release, or a commit it will not report. Handlers cannot help, because the gap
    is *before* the fact is recorded; the only fix is for the signal not to arrive yet.

    Blocking is unavailable on some platforms, where this degrades to the previous behaviour
    rather than failing."""
    numbers = [getattr(signal, name) for name in _ABORT_SIGNALS if hasattr(signal, name)]
    if not numbers or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, numbers)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


class _AbortOnSignal:
    """Run `cleanup` if a catchable termination signal arrives, then die as we would have.

    SIGKILL cannot be caught, so a hard kill can still leave the index lock behind. That is the
    same exposure ordinary `git commit` has -- it leaves `index.lock` too -- and it cannot
    publish anything, because the transaction only commits on a `commit` line this process never
    gets to send."""

    def __init__(self, cleanup):
        self.cleanup = cleanup
        self.previous = {}

    def __enter__(self):
        for name in _ABORT_SIGNALS:
            number = getattr(signal, name, None)
            if number is None:
                continue
            try:
                self.previous[number] = signal.signal(number, self._handle)
            except (ValueError, OSError):
                pass  # not the main thread, or unsupported on this platform
        return self

    def __exit__(self, *unused):
        for number, handler in self.previous.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                pass
        self.previous = {}

    def _handle(self, number, frame):
        try:
            self.cleanup()
        finally:
            # Restore and re-raise, so the caller's own handling and exit status are what they
            # would have been if this class were not here.
            try:
                signal.signal(number, self.previous.get(number, signal.SIG_DFL))
            except (ValueError, OSError):
                pass
            os.kill(os.getpid(), number)


class CommitError(RuntimeError):
    """The commit cannot proceed. Nothing public has moved."""


class CommitCancelled(CommitError):
    """Cancelled before publication, so no commit exists and none will appear later."""


class CommitPublished(CommitError):
    """The commit IS published, and something after that failed.

    Kept distinct because the obvious response to a plain failure is to commit again, and doing
    that here produces a duplicate. A caller must report the commit named by `.commit` and must
    not retry. It subclasses `CommitError` so existing handling still catches it rather than
    letting it escape as an unhandled error."""

    def __init__(self, commit, detail):
        super().__init__(detail)
        self.commit = commit


def _config_quote(value):
    value = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + value.replace("\n", "\\n").replace("\t", "\\t").replace("\b", "\\b") + '"'


def _captured_config(records):
    """Serialize effective Git config, with includes and conditional sections already resolved.

    Resolved at the real repository, because `includeIf` conditions depend on where the
    repository is; re-evaluating them from the private directory would silently pick up
    different settings than the person's own commits use."""
    lines = []
    for record in records.split("\0"):
        if not record:
            continue
        key, separator, value = record.partition("\n")
        if key.lower().startswith(("include.", "includeif.")):
            continue  # already resolved; re-including would re-evaluate the condition
        prefix, _, name = key.rpartition(".")
        section, has_subsection, subsection = prefix.partition(".")
        lines.append("[{}{}]".format(
            section, " " + _config_quote(subsection) if has_subsection else ""
        ))
        lines.append(name + (" = " + _config_quote(value) if separator else ""))
    return "\n".join(lines) + "\n"


class CommitTransaction:
    """Capture on entry, build a candidate, publish it, and own nothing else."""

    def __init__(self, root):
        self.root = str(root)
        self.environment = None
        self.git_environment = None
        self.temporary = None
        self.tree = None
        self.parent = ""

    # -- plumbing ---------------------------------------------------------------------------
    def git(self, *args, **kwargs):
        # Never the bare inherited environment: a live-repository command must act on `root`,
        # not on whatever GIT_DIR the caller happened to export.
        environment = kwargs.pop("environment", None) or _clean_environment()
        check = kwargs.pop("check", True)
        result = subprocess.run(
            ["git", "-C", self.root] + list(args), env=environment,
            capture_output=True, text=True, errors="replace", timeout=30,
        )
        if check and result.returncode:
            raise CommitError(result.stderr.strip() or "Git could not capture the commit state")
        return result.stdout.strip()

    def __enter__(self):
        try:
            self._capture()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *unused):
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    # -- capture ----------------------------------------------------------------------------
    def _capture(self):
        match = _VERSION_RE.search(self.git("--version"))
        if not match or tuple(int(part) for part in match.groups()) < MINIMUM_GIT:
            raise CommitError(
                "reviewed commits need Git {}.{} or newer for reference "
                "transactions".format(*MINIMUM_GIT)
            )
        self.gitdir = Path(self.git("rev-parse", "--absolute-git-dir"))
        self.index = Path(self.git("rev-parse", "--path-format=absolute", "--git-path", "index"))
        objects = self.git("rev-parse", "--path-format=absolute", "--git-path", "objects")
        if self.git("rev-parse", "--show-ref-format") != "files":
            raise CommitError("reviewed commits currently need Git's files reference format")
        self._check_in_progress()

        self.head = (self.gitdir / "HEAD").read_bytes()
        self.parent = self.git("rev-parse", "--verify", "HEAD", check=False)
        decoded = self.head.decode("utf-8", "replace").strip()
        self.target = decoded[5:] if decoded.startswith("ref: ") else "HEAD"
        self.zero = "0" * (64 if self.git("rev-parse", "--show-object-format") == "sha256" else 40)

        # Private Git state lives INSIDE the repository's metadata so it shares a filesystem with
        # the object database and is removed with the repository if anything goes very wrong.
        self.temporary = tempfile.TemporaryDirectory(
            prefix="reviewer-commit-", dir=str(self.gitdir)
        )
        self.private = Path(self.temporary.name)
        (self.private / "objects").mkdir()
        (self.private / "refs").mkdir()
        (self.private / "HEAD").write_bytes(self.head)
        references = self.git("for-each-ref", "--format=%(objectname) %(refname)")
        (self.private / "packed-refs").write_text(
            references + "\n" if references else "", encoding="utf-8"
        )
        shutil.copyfile(str(self.index), str(self.private / "index"))
        for shared in self.gitdir.glob("sharedindex.*"):  # a split index names its shared file
            shutil.copyfile(str(shared), str(self.private / shared.name))

        config = self.private / "config"
        config.write_text(
            _captured_config(self.git("config", "--null", "--list", "--includes")),
            encoding="utf-8",
        )
        self.git("config", "--file", str(config), "core.bare", "false")
        self.git("config", "--file", str(config), "core.worktree", self.root)
        hooks = self.git("rev-parse", "--path-format=absolute", "--git-path", "hooks")
        self.git("config", "--file", str(config), "core.hooksPath", hooks)

        self.git_environment = dict(
            GIT_DIR=str(self.private), GIT_COMMON_DIR=str(self.private),
            GIT_INDEX_FILE=str(self.private / "index"), GIT_WORK_TREE=self.root,
            GIT_OBJECT_DIRECTORY=objects,
            GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        )
        self.environment = dict(_clean_environment(), **self.git_environment)

        if self.git("rev-parse", "--verify", "HEAD",
                    environment=self.environment, check=False) != self.parent:
            raise CommitError("HEAD changed while capturing the review; retry the commit")
        self.tree = self.git("write-tree", environment=self.environment)
        if not self.matches_live():
            raise CommitError(
                "the staged set or HEAD changed while capturing the review; retry the commit"
            )

    def _check_in_progress(self):
        for name in _IN_PROGRESS:
            if (self.gitdir / name).exists():
                raise CommitError(
                    "a merge, rebase or sequencer operation is in progress, whose parents and "
                    "message the gate must not silently replace with an ordinary commit's"
                )

    # -- comparison -------------------------------------------------------------------------
    def matches_live(self):
        """Is the live repository still the one that was reviewed?

        Compares a COPY of the live index rather than reading it in place, so that merely
        checking can never take the index lock or rewrite the person's index."""
        if (self.gitdir / "HEAD").read_bytes() != self.head:
            return False
        if self.git("rev-parse", "--verify", "HEAD", check=False) != self.parent:
            return False
        copy = self.private / "live-index"
        shutil.copyfile(str(self.index), str(copy))
        environment = dict(_clean_environment(), GIT_INDEX_FILE=str(copy))
        return self.git("write-tree", environment=environment) == self.tree

    def patch(self, limit):
        """`(text, truncated)` for `git diff --cached` of the CAPTURED index.

        For a reviewer handed a patch string rather than Git access (the claude backend). Read
        from the private snapshot, not the live index, so it is exactly the content publication
        will verify -- a stage that lands after capture is not in it. Mirrors
        reviewer_git.staged_patch's contract: `truncated` means the caller must refuse a verdict
        formed on a partial patch, exactly as a reviewer that never saw the diff is refused."""
        try:
            result = subprocess.run(
                # --no-ext-diff/--no-textconv: a repository's diff.external or a textconv filter
                # could otherwise hand the reviewer a FORGED patch while the real captured tree is
                # committed, so the reviewer would approve content it never saw.
                ["git", "-C", self.root, "diff", "--cached", "--no-color",
                 "--no-ext-diff", "--no-textconv"],
                env=self.environment, capture_output=True, text=True, errors="replace", timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            # A launch failure or a timeout is the caller's handled empty-patch path (fail-closed),
            # not an exception that escapes the hook -- the same contract staged_patch keeps.
            return "", False
        if result.returncode:
            return "", False
        text = result.stdout
        if len(text.encode("utf-8", "replace")) <= limit:
            return text, False
        return text[:limit], True

    # -- candidate --------------------------------------------------------------------------
    def candidate(self, message, options, cancel_event=None):
        """Commit into the private state and return `(sha, git output)`. Nothing public moves.

        The repository's real hooks run, because the alternative -- plumbing that skips them --
        would publish content the repository's own checks never saw. A hook that rewrites the
        content is caught afterwards by comparing the result against the reviewed tree."""
        if cancel_event is not None and cancel_event.is_set():
            raise CommitCancelled("the commit was cancelled before it was built")
        # The reviewer may have run tests in the worktree; the approved tree is still the
        # captured one, so a captured index that moved means the review no longer describes it.
        if self.git("write-tree", environment=self.environment) != self.tree:
            raise CommitError("the captured index changed during review; retry the commit")

        result = subprocess.run(
            ["git", "-C", self.root, "commit"] + list(options) + ["-m", message],
            env=self.environment, capture_output=True, text=True, errors="replace", timeout=300,
        )
        if cancel_event is not None and cancel_event.is_set():
            raise CommitCancelled("the commit was cancelled before publication")
        output = (result.stdout + result.stderr).strip()
        if result.returncode:
            raise CommitError("the review approved, but git commit failed: " + output)

        sha = self.git("rev-parse", "HEAD", environment=self.environment)
        headers = self.git(
            "cat-file", "-p", sha, environment=self.environment
        ).split("\n\n", 1)[0].splitlines()
        trees = [line[5:] for line in headers if line.startswith("tree ")]
        parents = [line[7:] for line in headers if line.startswith("parent ")]
        if trees != [self.tree] or parents != ([self.parent] if self.parent else []):
            raise CommitError(
                "a Git hook changed the reviewed tree or parents, so nothing was published"
            )
        return sha, output

    # -- publication ------------------------------------------------------------------------
    def _after_commit(self, process):
        """Close down the transaction process once the reference has already moved.

        Its own method so the post-publication path can be exercised: everything here runs
        AFTER the commit is public, and a failure in it must never be reported as a commit that
        did not happen."""
        process.stdin.close()
        if process.wait(timeout=10):
            raise RuntimeError(
                (process.stderr.read().strip() or "git update-ref exited non-zero")
            )

    def publish(self, candidate, message, cancel_event=None):
        """Move the real reference to `candidate`, or raise without having moved anything.

        Returns the published commit id.

        The live index lock is taken first, so nothing can stage while this runs, and the
        reference transaction is `prepare`d before the final comparison, so Git holds the
        reference locks too. That ordering is what removes the check-then-write window.

        Signal protection spans the WHOLE lock lifetime, not just the prepared window. A gap
        before the transaction starts would strand the lock, and a gap after `commit: ok` would
        kill the caller once the reference had already moved, with nothing reporting the commit
        that exists. Anything that fails after that point raises `CommitPublished`."""
        lock = Path(str(self.index) + ".lock")
        state = {"process": None, "done": False, "published": False, "owned": False}

        def release():
            """Abort anything prepared and give the lock back. Safe to call twice."""
            if state["done"]:
                return
            state["done"] = True
            process = state["process"]
            try:
                if process is not None:
                    try:
                        # Closing the input FIRST abandons a transaction that was prepared but
                        # never committed, so every reference stays where it was.
                        if not process.stdin.closed:
                            process.stdin.close()
                    except (BrokenPipeError, ValueError):
                        pass
                    if process.poll() is None:
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                    for stream in (process.stdout, process.stderr):
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
            finally:
                # Only ever this attempt's own lock. `owned` is set solely on a successful
                # O_EXCL create, so a lock another Git process already held is left untouched.
                # `missing_ok` is not on the supported Python floor, so the race is explicit.
                if not state["owned"]:
                    return
                try:
                    lock.unlink()
                except FileNotFoundError:
                    pass

        # The handler is installed BEFORE the lock is taken and stays installed until after
        # cleanup has run. Those two intervals -- between acquiring the lock and protecting it,
        # and between the last protected statement and the cleanup -- are each long enough to
        # receive a signal, and a signal in either one strands the lock.
        with _AbortOnSignal(release):
          try:
            # Creating the lock and recording that we own it must be indivisible: a signal
            # between them leaves a lock that cleanup would decline to remove.
            with _blocked_signals():
                try:
                    descriptor = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    raise CommitError(
                        "another Git operation holds the index lock; retry the commit"
                    )
                state["owned"] = True
            os.close(descriptor)

            self._check_in_progress()
            # The child is NOT put in its own session: it must not outlive the process that
            # vouched for it.
            process = subprocess.Popen(
                ["git", "-C", self.root, "update-ref", "-m",
                 "commit: " + message.splitlines()[0], "--stdin"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=_clean_environment(),
            )
            state["process"] = process

            def exchange(commands, expected):
                process.stdin.write(commands)
                process.stdin.flush()
                line = process.stdout.readline().strip()
                if line != expected:
                    detail = line or (process.stderr.read().strip() if process.poll() is not None
                                      else "")
                    raise CommitError(
                        "Git refused the reference transaction: " + (detail or "no reason given")
                    )

            exchange("start\n", "start: ok")
            # Updating a symbolic HEAD prepares locks for HEAD and for its referent both, so the
            # branch cannot move underneath the check below.
            commands = "option no-deref\n" if self.target == "HEAD" else ""
            commands += "update HEAD {} {}\nprepare\n".format(candidate, self.parent or self.zero)
            exchange(commands, "prepare: ok")

            # Git holds the reference locks and this process holds the index lock. There is
            # no gap between this check and the write it guards.
            if not self.matches_live():
                raise CommitError(
                    "the staged set or HEAD changed after the review; retry the commit"
                )
            self._check_in_progress()
            if cancel_event is not None and cancel_event.is_set():
                raise CommitCancelled("the commit was cancelled before publication")

            # Moving the reference and recording that it moved must also be indivisible: a
            # signal between them would leave a real commit that nothing knows about.
            with _blocked_signals():
                exchange("commit\n", "commit: ok")
                # From here the reference HAS moved. Nothing below may be reported as a failure
                # to commit, because the natural response to that is to commit again.
                state["published"] = True
            try:
                self._after_commit(process)
            except Exception as error:
                raise CommitPublished(
                    candidate,
                    "the commit was published as {}, but finishing the reference transaction "
                    "failed: {}".format(candidate, error),
                )
            return candidate
          finally:
            release()
