#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Git layer of the reviewer package: what a command targets, and what is staged.

Step 3 of LIB_SPLIT_PLAN.md. Like `reviewer_transcript`, this module imports NOTHING from the
package -- stdlib only -- so it is a third independent root. `tools/check_split_layering.py`
fails if that ever stops being true.

Two jobs, and the split between them matters. The `git ...` LEXICAL parsers decide what a
command would do before it runs: which repository it targets, and whether it would commit
content the staged-only audit never saw. They are best-effort by design -- an unparseable
command falls back to the safe answer rather than raising -- and they are biased toward
precision, since a false positive hijacks an unrelated command while a false negative merely
lets one through unreviewed. The `subprocess` helpers then ask git itself what is true.

`git_toplevel` and `_TOPLEVEL_CACHE` are deliberately NOT here: they belong to the session
manifest and move at step 4.

Everything here is re-exported by `reviewer_lib` for back-compat, so existing
`rl.staged_files(...)` call sites are unchanged.
"""
import os
import re
import shlex
import subprocess

# Recognize a real `git ... commit` invocation. Biased toward precision: a false
# positive would hijack a non-commit, while a false negative merely lets a commit
# through unreviewed. Matches `git commit`, `git -C <path> commit`, and the same
# after a separator/env-prefix; `commit` must be the subcommand token (so
# `git committed` and `git commit-tree` do NOT match).
_COMMIT_RE = re.compile(r"(?:^|[\s;&|()])git\s+(?:[^\s;&|]+\s+)*commit(?=$|[\s;&|])")

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

# A `-C <path>` argument the LEXER cannot resolve because it carries shell expansion only the
# shell performs at run time: a `$VAR`/`${VAR}`, a `$(...)` command substitution, or a `...`
# backtick. shlex keeps the token literal, so `os.path.join(cwd, "$R")` used to fabricate
# `cwd/$R` -- no real repository -- and is_active() then came back False, silently disabling the
# fail-CLOSED commit gate (the amazon-reviews fail-open, where every commit was `git -C "$R"
# commit`). Such a target is unknowable statically, so commit_hook FAILS THE COMMIT CLOSED
# (commit_c_unresolvable + any_reviewer_active) rather than gating the session cwd -- gating cwd
# would review the WRONG repo when the variable points at a different, active repository.
# `git_c_effective_dir` still returns cwd for such a target, but only as an internal fallback the
# fail-closed check pre-empts; it is never used to gate the commit.
_UNRESOLVABLE_C_RE = re.compile(r"[$`]")


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
    return _COMMIT_RE.search(command or "") is not None


def _commit_c_scan(command, cwd):
    """Walk `command` for the `git ... commit` invocation and resolve its `-C` target.

    Returns `(effective_dir, unresolvable)`:
    - `effective_dir` is the directory the commit acts on. Git applies -C options (GLOBAL, before
      the subcommand) in order -- each relative to the prior, an absolute path resetting -- and
      chdir's there before running the subcommand, so the reviewer must gate the repo the commit
      TARGETS, not the Claude session's cwd. It is `cwd` when there is no -C, no commit subcommand,
      or the command cannot be lexically parsed.
    - `unresolvable` is True when a -C target on the commit invocation carries shell expansion
      (`$VAR`, `$(...)`, backtick) the lexer cannot expand. The gate then cannot know which
      repository the commit targets: `effective_dir` falls back to `cwd`, but the caller MUST fail
      CLOSED on it rather than gate `cwd` -- gating `cwd` reviews the WRONG repo (a fail-open) when
      the variable points elsewhere."""
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
        unresolvable = False  # a -C target this invocation carries shell expansion we cannot expand
        k = j + 1
        while k < n:  # walk this git invocation's GLOBAL options up to its subcommand
            t = tokens[k]
            if t in _SHELL_OPS or _REDIR_RE.match(t):
                break
            if t == "-C" and k + 1 < n:
                if _UNRESOLVABLE_C_RE.search(tokens[k + 1]):
                    unresolvable = True  # e.g. `git -C "$R" commit` -- target unknowable
                else:
                    eff = os.path.join(eff, os.path.expanduser(tokens[k + 1]))
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
                if unresolvable:  # target unknowable -> caller fails CLOSED; dir is just a fallback
                    return cwd, True
                return (os.path.normpath(eff) if saw_c else cwd), False
            break
        j = k + 1
    return cwd, False


def git_c_effective_dir(command, cwd):
    """Effective working directory a `git ... commit` acts on, honoring `git -C <path>`. Returns
    the effective directory, or `cwd` unchanged when there is no -C, no commit subcommand, the
    command cannot be lexically parsed, or the -C target is an unresolvable shell expansion (for
    which the caller must additionally consult `commit_c_unresolvable` and fail CLOSED)."""
    return _commit_c_scan(command, cwd)[0]


def commit_c_unresolvable(command):
    """True when the `git ... commit` invocation in `command` carries a `-C <target>` whose target
    is a shell expansion the gate cannot resolve (`$VAR`, `$(...)`, backtick). The gate cannot know
    which repository such a commit targets, so commit_hook fails CLOSED on it rather than reviewing
    the session cwd -- which is the WRONG repo (a fail-open) when the variable points elsewhere.
    Regression for the amazon-reviews fail-open, where `R=<repo>; git -C "$R" commit` slipped
    through unreviewed."""
    return _commit_c_scan(command, "")[1]


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


# The `claude` backend has no git access at all -- no shell, and its read tool sees the working
# TREE rather than the index -- so the patch has to travel in the prompt. Bounded, because a
# prompt is not a stream: an unbounded diff would either be refused by the model or silently
# truncated by it, and a reviewer that saw half a patch must never be able to approve it.
STAGED_PATCH_MAX = 400000


def staged_patch_max():
    raw = os.environ.get("REVIEWER_STAGED_PATCH_MAX")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return STAGED_PATCH_MAX
    return value if value > 0 else STAGED_PATCH_MAX


def staged_patch(path, limit=None):
    """(patch text, truncated) for `git diff --cached`.

    `truncated` is not cosmetic: the caller must refuse to accept a verdict formed on a partial
    patch, exactly as the pi backend refuses one formed without reading the patch at all.
    """
    limit = staged_patch_max() if limit is None else limit
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "diff", "--cached", "--no-color"],
            capture_output=True, text=True, errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", False
    if result.returncode != 0:
        return "", False
    text = result.stdout
    if len(text.encode("utf-8", "replace")) <= limit:
        return text, False
    return text[:limit], True
