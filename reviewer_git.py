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

from reviewer_base import terminate_process_tree

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


# The flags the gate can carry onto a commit it performs itself. Pinned to the set the Codex
# implementation accepts, deliberately: deciding case by case what looks "safe enough" is how a
# pinned set becomes open-ended option support.
_CARRIED_FLAGS = ("--signoff", "--quiet")
# Characters that make a BARE word mean something other than itself to a shell. Stated as a set
# of characters rather than a list of expansions so the rule is complete: globbing, tilde and
# brace expansion, history and substitution are all refused without enumerating what each does.
_BARE_WORD_SPECIAL = set("$`*?[]{}~!\\\"'")
# Before the subcommand the same rule applies MINUS `~`, which is handled separately because
# only SOME tilde forms are safe: a repository target may be written `git -C ~/repo`, and
# `git_c_effective_dir` resolves that with expanduser exactly as the shell would, but a shell
# also expands `~+`, `~-` and `~1` from the directory stack and expanduser does not. Every other
# shell-active character still has to go: a glob in a `-C` target expands to a real path for git
# while the gate resolves the literal text, so the gate would audit one repository while the
# commit lands in another.
_TARGET_SPECIAL = _BARE_WORD_SPECIAL - set("~")
# The tilde-prefixes a shell takes from its directory stack: `+`, `-`, and an optionally signed
# number. These are the ONLY forms where a shell and expanduser disagree -- `~`, `~/...` and
# `~name` all resolve identically in both -- so they are the only ones worth refusing.
# An EMPTY prefix (`~` alone, or `~/...`) must not match: that is the plain home-directory form
# both resolve the same way.
_DIRECTORY_STACK_TILDE = re.compile(r"\A(?:[+-][0-9]*|[0-9]+)\Z")
# Inside double quotes a shell resolves `\"` and `\\`, and so does the lexer, so those reach git
# as the same text either way. It also resolves `\$` and a backslash-escaped backtick, and the
# lexer does NOT -- it keeps the backslash. The gate cannot reproduce that value faithfully, so
# it refuses rather than committing text that differs from what was written by one character.
_DOUBLE_QUOTE_ESCAPES = set('"\\')
_UNREPRODUCIBLE_ESCAPES = set("$`")


def _join_continuations(text):
    """Remove `\\<newline>` line continuations, which a shell erases before the command runs.

    The lexer keeps them as a literal newline inside a token, so without this a continued
    command arrives with a stray newline argument.

    Quote state has to be tracked properly rather than scanned for: an apostrophe inside a
    DOUBLE-quoted argument is ordinary text, and treating it as the start of a quoted span left
    a later continuation in place, which would have committed a different message from the one
    asked for. Single quotes are the one context where a backslash-newline survives, because a
    shell expands nothing at all inside them."""
    out = []
    i = 0
    n = len(text or "")
    quote = None
    while i < n:
        char = text[i]
        if quote == "'":  # everything is literal until the closing quote
            out.append(char)
            if char == "'":
                quote = None
            i += 1
            continue
        if char == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2  # removed by a shell, unquoted and inside double quotes alike
            continue
        if char == "\\" and i + 1 < n:
            out.append(char)  # any other escape is left for the lexer to resolve
            out.append(text[i + 1])
            i += 2
            continue
        out.append(char)
        if quote == '"':
            if char == '"':
                quote = None
        elif char in "'\"":
            quote = char
        i += 1
    return "".join(out)


def _posix_tokens(command):
    """The argument values git would receive, with shell punctuation split out.

    Posix mode does all the quoting work correctly, which is the point: a quoted `&&`, `;` or
    newline stays inside its token and only a REAL operator becomes one, so no hand-rolled
    boundary logic is needed and a multiline or escape-carrying message survives intact."""
    lexer = shlex.shlex(command or "", posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _expansion_outside_quotes(command):
    """Why this command's text would not reach git as written, or None.

    Answered from the RAW text, because quote removal has already happened by the time there are
    tokens: afterwards `'cost $5'` and `"$MSG"` are indistinguishable. One left-to-right pass,
    one verdict per lexical context, so the rule is complete rather than a list that grows with
    each expansion someone remembers.

    Two scopes, because they protect different things:

      * Everywhere: a `$` or backtick outside single quotes, whose value the gate cannot know,
        and a NEWLINE outside quotes, which separates commands exactly as `;` does. Folding that
        newline into whitespace would let a following line's words arrive as more arguments to
        the commit.
      * Everywhere: every other shell-active character too, EXCEPT that `~` is allowed before
        the subcommand. A `~` target is resolved by `git_c_effective_dir` exactly as the shell
        resolves it, so refusing it would drop support the gate already has; a glob or brace in
        that target is not, and would let the gate audit one repository while the commit lands
        in another.
    """
    text = command or ""
    i = 0
    n = len(text)
    word = []
    seen_commit = False
    while i < n:
        char = text[i]
        if char == "'":  # single quotes expand nothing at all
            close = text.find("'", i + 1)
            if close < 0:
                return "the command has an unterminated single quote, so its text is ambiguous"
            word.append(text[i + 1:close])
            i = close + 1
            continue
        if char == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in _UNREPRODUCIBLE_ESCAPES:
                    return (
                        "a backslash-escaped `{}` inside double quotes means a literal character "
                        "to a shell, but the gate's lexer keeps the backslash, so the committed "
                        "text would differ by one character; single-quote the message "
                        "instead".format(text[i + 1])
                    )
                if text[i] == "\\" and i + 1 < n and text[i + 1] in _DOUBLE_QUOTE_ESCAPES:
                    word.append(text[i + 1])
                    i += 2  # `\"` and `\\` resolve identically in a shell and in the lexer
                    continue
                if text[i] in "$`":
                    return (
                        "the shell would expand `{}` before git ever saw it, so the gate cannot "
                        "know the resulting text; use a literal message, or single-quote it so "
                        "the characters stay as written".format(text[i])
                    )
                word.append(text[i])
                i += 1
            if i >= n:
                return "the command has an unterminated double quote, so its text is ambiguous"
            i += 1
            continue
        if char == "\\" and i + 1 < n and text[i + 1] == "\n":
            i += 2  # a line continuation joins two lines into a single command
            continue
        if char == "\n":
            if text[i + 1:].strip():
                return (
                    "a newline outside quotes separates commands, so this call holds more than "
                    "one of them. The gate carries only a single `git commit`, and the rest "
                    "would be dropped without running; send it as its own tool call."
                )
            i += 1
            continue
        if char in " \t":
            if "".join(word) == "commit":
                seen_commit = True
            del word[:]
            i += 1
            continue
        if char in "$`":
            return (
                "the shell would expand `{}` before git ever saw it, so the gate cannot know "
                "the resulting text; quote it so the characters stay as written".format(char)
            )
        if char == "~":
            # A shell expands a tilde-prefix ONLY at the start of a word, and the prefix runs to
            # the first `/`. Mid-word it is ordinary text, so `a~b` and `/tmp/repo~` are literal
            # to both a shell and the gate and need no refusal at all.
            if word:
                word.append(char)
                i += 1
                continue
            prefix = ""
            j = i + 1
            while j < n and text[j] not in "/ \t\n":
                prefix += text[j]
                j += 1
            if seen_commit:
                # In a MESSAGE any leading tilde expands, so the committed text would not be
                # the text written here.
                return (
                    "a leading `~` is expanded by a shell, so the committed message would not "
                    "be the text written here; quote the message"
                )
            if _DIRECTORY_STACK_TILDE.match(prefix):
                # `~+`, `~-` and `~1` come from the directory stack, which expanduser knows
                # nothing about: it hands them back untouched, so the gate would resolve the
                # literal text and audit a different repository from the one git targets.
                return (
                    "`~{}` is expanded from the shell's directory stack, which the gate cannot "
                    "reproduce, so the commit could land in a different repository from the one "
                    "reviewed; use an absolute path".format(prefix)
                )
            # `~`, `~/...` and `~name` are all resolved identically by a shell and by the
            # expanduser that git_c_effective_dir already applies.
            word.append(char)
            i += 1
            continue
        if char in (_BARE_WORD_SPECIAL if seen_commit else _TARGET_SPECIAL):
            return (
                "the unquoted `{}` is something a shell acts on rather than passes through, so "
                "the text git receives would not be the text written here; quote it".format(char)
            )
        word.append(char)
        i += 1
    return None


def _unsupported_reason(token):
    """Why this commit argument cannot be carried, in words the agent can act on."""
    name = token.split("=", 1)[0]
    if name in ("--amend",):
        return ("`--amend` replaces the commit's parent, and the reviewed snapshot is bound to "
                "the parent it was reviewed against; amending is not carried")
    if name in ("-F", "--file", "-C", "--reuse-message", "-c", "--reedit-message",
                "--squash", "--fixup", "--template", "-t"):
        return ("`{}` takes the message from somewhere the gate did not review; pass the "
                "message with -m".format(name))
    if name in ("--author", "--date"):
        return "`{}` overrides commit identity, which the gate does not carry".format(name)
    if name in ("--no-verify", "-n"):
        return "`{}` skips the repository's own hooks, which the gate runs deliberately".format(name)
    if name in ("-S", "--gpg-sign", "--no-gpg-sign"):
        return ("`{}` changes signing, which follows the repository's configuration "
                "instead".format(name))
    if name == "--":
        return "a pathspec commits content outside the reviewed staged set"
    if not token.startswith("-"):
        # Either a pathspec, or a word belonging to another command that shared this call --
        # a newline-separated second command arrives here, its words looking like arguments.
        return (
            "the gate carries only a single `git commit`, and `{}` is not part of one: as a "
            "pathspec it would commit content outside the reviewed staged set, and as part of "
            "another command it would be dropped without running. Send it as its own tool "
            "call.".format(token)
        )
    return "`{}` is not one of the options the gate carries".format(token)


def parse_commit_request(command):
    """`(request, None)` for a call the gate can perform itself, else `(None, reason)`.

    Once the gate commits and DENIES the tool call, whatever else was in that call is dropped
    without running -- `git commit -m x && git push` would publish and silently skip the push.
    So the call has to be exactly one `git commit`, and the message and flags have to be
    recovered from it exactly. Anything unrecognised is refused rather than half-performed,
    which is the safe direction. See docs/plans/claude_commit_transaction_design.md."""
    # Continuations are removed FIRST, once, so everything downstream reasons about the command
    # a shell would actually run. Classifying a tilde-prefix on the raw text let
    # `git -C ~+\<newline>/sub` through: the prefix read as `+\` rather than the `+` a shell sees
    # once it has joined the lines.
    command = _join_continuations(command or "")
    expansion = _expansion_outside_quotes(command)
    if expansion:
        return None, expansion
    try:
        tokens = _posix_tokens(command)
    except ValueError:
        return None, "the command could not be parsed, so the gate cannot know what it would do"
    if not tokens:
        return None, "there is no command to run"

    for index, token in enumerate(tokens):
        if token in _SHELL_OPS or _REDIR_RE.match(token):
            rest = " ".join(tokens[index + 1:]).strip()
            return None, (
                "the gate performs the commit itself and then declines this tool call, so "
                "anything else in the call would be dropped without running{}. Send only the "
                "`git commit`, and run the rest as its own tool call.".format(
                    " (here: {})".format(rest) if rest else ""
                )
            )
    if tokens[0] != "git":
        return None, (
            "the gate carries only a single plain `git commit`, and this call starts with "
            "something else, which would be dropped without running"
        )

    index = 1
    while index < len(tokens) and tokens[index].startswith("-"):
        name = tokens[index].split("=", 1)[0]
        if name != "-C":  # honored already by the gate; every other global changes the target
            return None, (
                "`{}` before the subcommand changes which repository or configuration the "
                "commit uses, which the gate does not carry".format(name)
            )
        if "=" in tokens[index]:
            # git wants `-C <path>` and rejects `-C=path`; git_c_effective_dir also ignores the
            # `=` form and falls back to the session cwd, so accepting it would let the gate
            # commit in a different directory from the one the command names.
            return None, (
                "`-C` takes its path as a separate argument (`-C <path>`); the `-C=path` form is "
                "not valid git, and the gate will not guess which repository it means"
            )
        index += 2
    if index >= len(tokens) or tokens[index] != "commit":
        return None, "this is not a `git commit`"

    messages = []
    options = []
    args = tokens[index + 1:]
    position = 0
    while position < len(args):
        token = args[position]
        if token in _CARRIED_FLAGS:
            if token not in options:
                options.append(token)
            position += 1
            continue
        if token in ("-m", "--message"):
            if position + 1 >= len(args):
                return None, "`{}` was given without a message".format(token)
            # Expansion was ruled out over the whole command above, so the lexed token IS the
            # text git would receive, quoting and escapes already resolved.
            messages.append(args[position + 1])
            position += 2
            continue
        if token.startswith("--message="):
            messages.append(token.split("=", 1)[1])
            position += 1
            continue
        return None, _unsupported_reason(token)

    if not messages:
        return None, (
            "no commit message was given, so git would open an editor, which cannot work from "
            "inside the gate; pass the message with -m"
        )
    # Git joins repeated -m as separate paragraphs.
    return {"message": "\n\n".join(messages), "options": options}, None


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
            # --no-ext-diff/--no-textconv: a repo's diff.external or textconv filter could
            # otherwise show the reviewer a forged patch while the real staged tree is what
            # gets committed, so the reviewer would approve content it never saw.
            ["git", "-C", str(path), "diff", "--cached", "--no-color",
             "--no-ext-diff", "--no-textconv"],
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


def staged_tree(path):
    """`git write-tree` of the current index -- the digest of the staged set (content, file
    modes, names and structure), or "" if it cannot be computed.

    This is the whole integrity check for the gate: the hash recorded when the reviewer saw the
    staged set is compared to this at commit time; equal means what is about to be committed is
    byte-for-byte what was reviewed. Git already maintains it (the index cache-tree), so this is
    cheap. write-tree persists tree objects as a side effect, which is harmless and idempotent."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "write-tree"],
            capture_output=True, text=True, errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def run_commit(path, message, options, timeout=300):
    """Run a normal `git commit` -- the repository's own hooks run, exactly as configured.

    Returns `(status, commit, detail)`:

      * `("committed", sha, note)` -- a commit was published. `sha` is its id, or "" when git
        exited cleanly but the id could not be read back. `note` is "" for a clean commit, or a
        warning when git did not exit cleanly afterwards (a failing or hung hook).
      * `("failed", None, error)` -- NOTHING was published; retrying is safe.
      * `("unknown", None, detail)` -- publication could NOT be determined (HEAD was unreadable
        after an unclean exit). The caller must NOT report "nothing committed" nor retry blindly.

    A timeout or a nonzero exit is ambiguous -- git may already have moved the ref before a later
    step failed -- so publication is decided by whether HEAD advanced, and by whether git exited
    cleanly when HEAD cannot be read, never assumed to be "nothing committed" (which would prompt a
    duplicate). The message is passed as an argument vector, never through a shell; the gate does
    not second-guess what the repo's hooks do to the content."""
    parent = git_head(path)
    argv = ["git", "-C", str(path), "commit"] + list(options) + ["-m", message]
    try:
        # A new session so a timeout can tear down git AND any hook it spawned. A plain subprocess
        # timeout kills only git, leaving a slow pre-commit hook running to mutate or re-stage
        # files after the gate has already reported failure and the caller may be retrying.
        proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace", start_new_session=True,
        )
    except OSError as error:
        return "failed", None, str(error)  # git never launched -> nothing was published
    try:
        out, err = proc.communicate(timeout=timeout)
        returncode, detail = proc.returncode, ((out or "") + (err or "")).strip()
        clean_exit = returncode == 0
    except subprocess.TimeoutExpired:
        terminate_process_tree(proc.pid)  # git and every hook process it spawned
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        detail, clean_exit = "git commit timed out and was terminated", False
    head = git_head(path)
    if head is None:
        # HEAD could not be read. A clean exit means a commit WAS made but cannot be named; an
        # unclean exit leaves publication genuinely undetermined -- never collapse either to
        # "nothing committed".
        if clean_exit:
            return "committed", "", "the commit was created but its id could not be read"
        return "unknown", None, (
            "{}; the repository state could not be read afterwards, so whether a commit was "
            "published is unknown".format(detail or "git commit did not complete"))
    published = head != parent
    if published and clean_exit:
        return "committed", head, ""
    if published:
        # The ref moved even though git commit did not exit cleanly -- the commit IS public and
        # must never be reported as "nothing committed" (that would prompt a duplicate).
        return "committed", head, (
            "git commit did not exit cleanly after publishing {}: {}".format(head, detail or "no detail"))
    if clean_exit:
        return "failed", None, detail or "git commit reported success but created no commit"
    return "failed", None, detail or "git commit failed"
