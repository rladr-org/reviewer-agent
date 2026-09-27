#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Lexically identify git commits and inspect the staged index."""
import os
import re
import shlex
import subprocess
import tempfile
import time

from .process_tree import terminate_process_tree
from .command_intent import commit_intent

COMMIT_TIMEOUT = 300

# Short options to `git commit` that consume the FOLLOWING token as their value
# (so that value is NOT mistaken for a pathspec): -m -F -C -c -t.
_COMMIT_VALUE_SHORT = set("mFCct")
# Long options to `git commit` that take a value as the next token when not `name=value`.
_COMMIT_VALUE_LONG = {
    "--message", "--file", "--reuse-message", "--reedit-message", "--fixup",
    "--squash", "--template", "--author", "--date", "--cleanup", "--gpg-sign",
    "--pathspec-from-file", "--trailer",
}
# Git GLOBAL options (before the subcommand) that consume the next token as a value.
_GIT_GLOBAL_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix",
}
# Shell operators that terminate one command in a compound command line.
_SHELL_OPS = {"&&", "||", "|", "|&", ";", "&"}
# Shell REDIRECTION operators (single shlex tokens): `>`, `>>`, `2>`, `2>&1`, `&>`, `<`, etc.
# These are NOT git arguments, so the commit-arg scan must stop at them -- otherwise
# `git commit -m x 2>&1 | tail` leaves `2>&1` looking like a bare pathspec (false positive).
_REDIR_RE = re.compile(r"^(?:[0-9]*[<>]|&>>?)")
# A hook sees shell source, not the expanded argv. If any ``-C`` operand uses
# expansion or shell syntax, its real target is unknowable and cannot authorize a review token.
_SHELL_DEPENDENT_PATH_CHARS = frozenset("~$`*?[]{}<>()!")


def is_git_repo(path):
    try:
        p = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return p.returncode == 0 and p.stdout.strip() == "true"


def is_git_commit(command):
    """Whether shell text visibly invokes a commit-producing operation."""
    return commit_intent(command, None).kind is not None


def _git_commit_context(command, cwd):
    """Return the effective directory and whether ``-C`` has an absolute anchor."""
    try:
        tokens = shlex.split(command or "")
    except ValueError:
        return cwd, False
    n = len(tokens)
    j = 0
    while j < n:
        if tokens[j] != "git":
            j += 1
            continue
        eff = str(cwd)
        saw_c = False
        absolute_anchor = False
        literal_targets = True
        k = j + 1
        while k < n:  # walk this git invocation's GLOBAL options up to its subcommand
            t = tokens[k]
            if t in _SHELL_OPS or _REDIR_RE.match(t):
                break
            if t == "-C" and k + 1 < n:
                # Accept only a literal absolute path. Shell-dependent forms such as
                # ``~`` or ``$REPO`` cannot be resolved reliably from a hook event.
                target = tokens[k + 1]
                if any(character in target for character in _SHELL_DEPENDENT_PATH_CHARS):
                    literal_targets = False
                if os.path.isabs(target):
                    eff = target
                    absolute_anchor = True
                else:
                    eff = os.path.join(eff, target)
                saw_c = True
                k += 2
                continue
            if t.startswith("-"):
                name = t.split("=", 1)[0]
                if name in _GIT_GLOBAL_VALUE and "=" not in t:
                    k += 2
                else:
                    k += 1
                continue
            if t == "commit":  # first non-option token is the subcommand
                return (
                    (os.path.normpath(eff) if saw_c else cwd),
                    absolute_anchor and literal_targets,
                )
            break
        j = k + 1
    return cwd, False


def git_c_effective_dir(command, cwd):
    """Effective working directory a `git ... commit` acts on, honoring `git -C <path>`.
    Git applies -C options (GLOBAL, before the subcommand) in order -- each relative to the
    prior, an absolute path resetting -- and chdir's there before running the subcommand. So
    the reviewer must gate the repo the commit targets, not the Codex session's cwd (e.g.
    `git -C /other/repo commit` issued from a session rooted elsewhere). Returns the effective
    directory, or `cwd` unchanged when there is no -C, no commit subcommand, or the command
    cannot be lexically parsed."""
    return _git_commit_context(command, cwd)[0]


def git_commit_has_absolute_target(command):
    """Whether global ``-C`` anchors the commit independently of an unknown workdir."""
    return _git_commit_context(command, ".")[1]


def _commit_arg_tokens(tokens):
    """Return the argument tokens of the `git commit` subcommand (up to the next shell
    operator), or None if there is no git-commit subcommand. Skips git GLOBAL options
    (e.g. `-C <path>`, `-c name=value`) when locating the subcommand, so a file named
    'commit' in another command is not mistaken for the subcommand."""
    n = len(tokens)
    j = 0
    while j < n:
        if tokens[j] != "git":
            j += 1
            continue
        k = j + 1
        while k < n:  # skip git global options to reach the subcommand token
            t = tokens[k]
            if t.startswith("-"):
                name = t.split("=", 1)[0]
                k += 2 if ("=" not in t and name in _GIT_GLOBAL_VALUE) else 1
                continue
            break
        if k < n and tokens[k] == "commit":
            args = []
            m = k + 1
            while m < n and tokens[m] not in _SHELL_OPS and not _REDIR_RE.match(tokens[m]):
                args.append(tokens[m])
                m += 1
            return args
        j = k + 1
    return None


def commits_beyond_index(command):
    """True if this `git commit` would include content BEYOND the staged index --
    i.e. `-a`/`--all` (also stage modified tracked files) or an explicit pathspec
    (`git commit <path>` / `git commit -- <path>` / `--include`/`--only`), which all
    commit working-tree content the staged-only audit (`git diff --cached`) never saw.
    The hook refuses these and asks for an explicit `git add` + a plain `git commit`,
    so the committed set always EQUALS the audited set. Plain `git commit`,
    `git commit -m msg`, and `git commit --amend` are NOT beyond the index.

    Best-effort lexical parse: an unparseable command returns False (the staged audit
    still runs). Flag values (e.g. the `-m` message, a `-C` commit ref) are skipped so
    they are never misread as pathspecs."""
    try:
        tokens = shlex.split(command or "")
    except ValueError:
        return False
    args = _commit_arg_tokens(tokens)
    if args is None:
        return False
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            # Everything after `--` is a pathspec -> beyond index if any are present.
            return i + 1 < len(args)
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name == "--all" or name in ("--include", "--only"):
                return True
            if "=" not in tok and name in _COMMIT_VALUE_LONG:
                i += 2  # skip the option's value token
                continue
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            cluster = tok[1:]
            if "a" in cluster or "i" in cluster or "o" in cluster:
                return True
            if cluster[-1] in _COMMIT_VALUE_SHORT:
                i += 2  # last short flag in the cluster consumes the next token
                continue
            i += 1
            continue
        # A bare non-option token after `commit` is a pathspec -> beyond index.
        return True
    return False


def git_head(path):
    """Current HEAD sha, or None if not a repo / no commits yet."""
    try:
        p = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def staged_files(path):
    """Names of files staged in the index; empty if nothing staged or not a repo."""
    try:
        p = subprocess.run(
            ["git", "-C", str(path), "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return []
    if p.returncode != 0:
        return []
    return [line for line in p.stdout.splitlines() if line.strip()]


def staged_tree(path):
    """Digest all staged content, modes, names, and directory structure."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "write-tree"], capture_output=True,
            text=True, errors="replace", timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def run_commit(path, message, options, cancel_event=None):
    """Run ordinary Git and report (status, id, output, warning/error).

    Hooks act on the real repository and may restage content. There is no private
    candidate or publication step. After interruption, inspect HEAD before reporting
    failure: Git may have published before a post-commit hook stalled.
    """
    if cancel_event is not None and cancel_event.is_set():
        return "cancelled", None, "", "commit cancelled before Git started"
    parent = git_head(path)
    reason = ""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                ["git", "-C", str(path), "commit", *options, "-m", message],
                cwd=str(path), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            )
        except OSError as error:
            return "error", None, "", "Git could not start: {}".format(error)
        try:
            deadline = time.monotonic() + COMMIT_TIMEOUT
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    reason = "Git commit was interrupted by cancellation"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    reason = "Git commit hooks/signing exceeded the {}s budget".format(COMMIT_TIMEOUT)
                    break
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                if os.name == "posix":
                    terminate_process_tree(process.pid, include_root=True)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
        # Files, rather than pipes, keep external hook helpers from holding EOF open.
        output = []
        for stream in (stdout, stderr):
            size = os.fstat(stream.fileno()).st_size
            stream.seek(0)
            output.append(stream.read(size).decode(errors="replace"))
        output = "".join(output).strip()
    head = git_head(path)
    if process.returncode == 0 and not reason:
        # A clean Git exit means it committed, even if a later hook moved HEAD again.
        if head and head != parent:
            return "committed", head, output, ""
        return "committed", None, output, "Git completed, but the new commit ID could not be confirmed"
    detail = reason or output or "Git commit failed"
    # HEAD movement alone cannot attribute publication to this invocation: hooks or
    # another writer may have moved it even though our commit failed.
    if head is None or head != parent:
        return "publication_unknown", None, output, (
            "Commit publication could not be determined. Do not retry automatically; "
            "check git log and git status first. " + detail)
    status = "cancelled" if cancel_event is not None and cancel_event.is_set() else "error"
    return status, None, output, detail + "; no commit was detected. Hooks may have modified or staged files; check git status."
