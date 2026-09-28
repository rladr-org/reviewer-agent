#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Git layer of the reviewer package: what a command targets, and what is staged.

Step 3 of LIB_SPLIT_PLAN.md. Like `reviewer_transcript`, this module imports NOTHING from the
package -- stdlib only -- so it is a third independent root. `tools/check_split_layering.py`
fails if that ever stops being true.

Two jobs, and the split between them matters. The `git ...` LEXICAL parsers decide what a
command would do before it runs: which repository it targets, and whether it would commit
content the staged-only audit never saw. Since the gate performs the reviewed commit itself and
refuses every other shape, detection is biased toward DETECTION: a false positive now costs an
accurate refusal, while a false negative commits unreviewed. `commit_intent` is the detector;
unparseable input falls back to the conservative `_COMMIT_RE`. The `subprocess` helpers then ask
git itself what is true.

`git_toplevel` and `_TOPLEVEL_CACHE` are deliberately NOT here: they belong to the session
manifest and move at step 4.

Everything here is re-exported by `reviewer_lib` for back-compat, so existing
`rl.staged_files(...)` call sites are unchanged.
"""
import os
import re
import shlex
import subprocess
from collections import namedtuple

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
    """Back-compat predicate: does `command` run any commit-producing git (see commit_intent)?"""
    return commit_intent(command, ".").kind is not None


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


_STAGING_SUBCOMMANDS = ("add", "rm", "mv", "stage")


def _git_subcommand(words):
    """The subcommand of a `git ...` word list (global options and `-C <path>` skipped), or None."""
    if not words or words[0] != "git":
        return None
    index = 1
    while index < len(words) and words[index].startswith("-"):
        name = words[index].split("=", 1)[0]
        index += 2 if (name in _GIT_GLOBAL_VALUE and "=" not in words[index]) else 1
    return words[index] if index < len(words) else None


def _compound_reason(tokens):
    """Why a call holding more than the one `git commit` is refused, naming its real cause.

    Staging in the same call is the common case, and the generic "would be dropped" text used to
    name the COMMIT as the dropped part -- the gate reads the index before the call runs, so the
    staging is what never happens."""
    segments, current = [], []
    for token in tokens:
        if token in _SHELL_OPS or _REDIR_RE.match(token):
            segments.append(current)
            current = []
        else:
            current.append(token)
    segments.append(current)
    others, commit_seen = [], False
    for segment in segments:
        if not segment:
            continue
        if not commit_seen and _git_subcommand(segment) == "commit":
            commit_seen = True  # the one the gate would perform; every other part is dropped
            continue
        others.append(" ".join(segment))
    staging = [" ".join(s) for s in segments if _git_subcommand(s) in _STAGING_SUBCOMMANDS]
    if staging:
        return (
            "this call stages and commits in one call ({}), but the gate reads the index BEFORE "
            "the call runs, so the staging would never happen first. Run `{}` as its own tool "
            "call, then a plain `git commit -m ...`".format(" && ".join(staging), staging[0])
        )
    return (
        "the gate performs the commit itself and then declines this tool call, so anything else "
        "in the call would be dropped without running{}. Send only the `git commit`, and run the "
        "rest as its own tool call.".format(" (here: {})".format("; ".join(others)) if others else "")
    )


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

    if any(token in _SHELL_OPS or _REDIR_RE.match(token) for token in tokens):
        return None, _compound_reason(tokens)
    if tokens[0] != "git" and os.path.basename(tokens[0]) == "git":
        return None, (
            "the gate runs `git` itself from PATH, so call it by its plain name "
            "(`git commit -m ...`), not `{}`".format(tokens[0])
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


# --------------------------------------------------------------------------
# commit intent: does this Bash call run a commit-producing git anywhere? (#15)
# --------------------------------------------------------------------------
CommitIntent = namedtuple("CommitIntent", "kind target reason")
_NO_INTENT = CommitIntent(None, None, None)
_MAX_DEPTH = 4
# Operators a shell recognises only when UNQUOTED, longest first so `&&` wins over `&`.
_OPERATORS = ("<<<", "<<-", "&>>", "&&", "||", ";;", "|&", "<<", ">>", "<&", ">&", "&>", "<>",
              ">|", "(", ")", ";", "|", "&", "<", ">")
_SEGMENT_OPS = _SHELL_OPS | {";;"}
_LIST_ENDS = {None, ";", ";;", "&"}
_PIPES = {"|", "|&"}
_COMPOUND_OPEN = {"if", "while", "until", "for", "case", "select", "{", "function"}
_COMPOUND_CLOSE = {"fi", "done", "esac", "}"}
_ASSIGNMENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
# Environment that points git at a repository or configuration the command text does not name.
_GIT_ENV_RE = re.compile(r"\AGIT_(?:DIR|WORK_TREE|COMMON_DIR|CONFIG[A-Z0-9_]*)=")
_GIT_LOCATION_OPTS = {"--git-dir", "--work-tree", "--config-env"}
_PREFIX_KEYWORDS = {"!", "{", "then", "do", "else", "elif", "if", "while", "until", "time"}
_REDIR_OP_RE = re.compile(r"\A[0-9]*(?:[<>]+[&|-]?|&>>?)\Z")
_HEREDOC_RE = re.compile(
    r'''<<(-?)[ \t]*((?:[^\s;&|<>()'"\\]+|'[^']*'|"(?:\\.|[^"\\])*"|\\[^\n])+)'''
)
# Characters that make a path only the shell can resolve (expansion, glob, brace expansion).
_PATH_SPECIAL = set("$`*?[]{}")
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_INTERPRETER_RE = re.compile(r"\A(?:python[0-9.]*|node|perl|ruby)\Z")
# An inline script that mentions git and then a commit-producing subcommand anywhere: argv
# lists with global options between them (`['git', '-C', d, 'commit']`) count, and so does
# text that assembles the command from pieces, at the cost of refusing a script that only
# talks about both.
# Hyphens belong to Git subcommands: merge-base and commit-graph are not producers.
_INLINE_GIT_COMMIT_RE = re.compile(
    r"\bgit\b[\s\S]*?(?<![\w-])(?:commit|commit-tree|merge|cherry-pick|revert|am|pull)(?![\w-])"
)

_PIPELINE_GIT_RE = _INLINE_GIT_COMMIT_RE
# Words by which an inline script of each interpreter runs a command. A script that mentions git
# and a producer WITHOUT one of these only carries the text as data. A bounded check, not
# interpreter analysis: a script that spawns something unrelated and also mentions a commit as
# text is still treated as committing. Backticks run a command only in perl and ruby.
_INLINE_SPAWN = {
    "python": r"\b(?:subprocess|system|popen|exec[lv]p?e?|spawn[lv]?p?e?|posix_spawnp?|pty)\b",
    "node": r"\b(?:child_process|exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\b",
    "perl": r"\b(?:system|exec|qx)\b|`|\bopen\b[^;\n]*\|",  # `open` runs a command only piped
    "ruby": r"\b(?:system|exec|spawn|popen\w*|Open3|capture\w*)\b|`|%x",
}
# An unresolved command name may be any of them.
_ANY_SPAWN_RE = re.compile("|".join("(?:{})".format(p) for p in _INLINE_SPAWN.values()))
# wrapper -> (options that take a value, options whose value is the directory the command runs
#             in, options whose value is a command line the wrapper splits and runs)
_WRAPPERS = {
    "env": ({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
            {"-C", "--chdir"}, {"-S", "--split-string"}),
    "command": (set(), set(), set()), "builtin": (set(), set(), set()),
    "exec": ({"-a"}, set(), set()), "nohup": (set(), set(), set()),
    "nice": ({"-n", "--adjustment"}, set(), set()),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, set(), set()),
    "sudo": ({"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t", "-T", "--user", "--group",
              "--close-from", "--chdir", "--host", "--prompt", "--other-user", "--role", "--type",
              "--command-timeout"}, {"-D", "--chdir"}, set()),
    "doas": ({"-u", "-C"}, set(), set()),
    "timeout": ({"-s", "--signal", "-k", "--kill-after"}, set(), set()),
    "xargs": ({"-I", "-n", "-L", "-d", "-E", "-P", "-s", "-a", "--arg-file", "--delimiter",
               "--max-args", "--max-procs", "--max-chars", "--max-lines", "--eof"}, set(), set()),
}
_PRODUCER_WORDS = {"commit", "commit-tree", "merge", "cherry-pick", "revert", "am", "pull"}
_RUNTIME_CODE_REASON = (
    "this runs a command the text does not contain -- one held in a variable set outside this "
    "call, or printed by another command -- so the gate cannot tell whether it commits; write "
    "the command out, or run a plain `git commit -m ...` as its own tool call")
# Set in `overrides` when a `-c` value is an expansion the call did not assign: it may define
# any key at all, an alias included.
_UNKNOWN_CONFIG = "\x00unknown"
_ALIAS_UNKNOWN_REASON = (
    "`git {}` may be an alias, and the gate cannot look it up: the text does not say which "
    "repository or configuration it runs with; use the full subcommand, a literal `-C` path, "
    "and literal `-c` values")
_EXPANSION_RE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")
_DECLARERS = {"export", "declare", "typeset", "local", "readonly"}
_UNRESOLVABLE_HEAD_REASON = (
    "the command name is a shell expansion the gate cannot resolve; call `git` by its name")
_INLINE_REASON = (
    "this inline script runs `git commit`, which the gate cannot review from outside; stage "
    "the change and run a plain `git commit -m ...` as its own tool call")
# Builtins cannot be shadowed by aliases, so an alias lookup is needed only for other words.
_GIT_BUILTINS = set(
    "add am apply archive bisect blame branch bundle cat-file check-attr check-ignore checkout "
    "cherry cherry-pick clean clone commit commit-graph commit-tree config count-objects "
    "describe diff diff-files diff-index diff-tree difftool fetch filter-branch for-each-ref "
    "format-patch fsck gc grep hash-object help init log ls-files ls-remote ls-tree merge "
    "merge-base mergetool mktag mktree mv name-rev notes pack-refs prune pull push range-diff "
    "read-tree rebase reflog remote repack replace rerere reset restore rev-list rev-parse "
    "revert rm shortlog show show-ref sparse-checkout stash status submodule switch "
    "symbolic-ref tag update-index update-ref var verify-commit verify-tag version worktree "
    "write-tree".split())
# A line ending in one of these operators continues on the next line.
_CONTINUED_RE = re.compile(r"(?:&&|\|\||\|&|\|)[ \t]*\Z")


def _split_script(text):
    """[(line, [(heredoc_body, expands), ...])] for each logical command line of `text`.

    Splits at newlines a shell treats as separators (outside quotes and `$(...)`/backquotes),
    drops `#` comments, and lifts each here-document body OUT of the command stream, returning
    the bodies beside the line that opened them, in the order their `<<` appear. `expands` is
    True for an unquoted delimiter, whose body the shell runs `$(...)` substitutions in. A body
    is otherwise data unless its command feeds it to a shell, which `_classify_segment` decides. A line ending in `&&`, `||` or `|` continues onto the next one,
    exactly as the shell continues it, so an and-or chain is never cut at a newline."""
    text = _join_continuations(text or "")
    lines, current, pending, bodies = [], [], [], []
    quote = None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            current.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
        elif c == "\\" and i + 1 < n:
            current.append(c)
            current.append(text[i + 1])
            i += 2
            continue
        elif c == "`" or text.startswith("$(", i):
            end = _substitution_end(text, i)  # a newline inside it separates nothing outside
            current.append(text[i:end])
            i = end
            continue
        elif c == "#" and (not current or current[-1] in " \t;&|("):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            m = _HEREDOC_RE.match(text, i)
            if m:
                # Quote removal applies to the entire delimiter word, including mixed
                # E"OF" and backslash-quoted forms. Any quoting makes the body literal.
                raw_word = m.group(2)
                word = shlex.split(raw_word)[0]
                expands = not any(char in raw_word for char in "\\'\"")
                pending.append((m.group(1) == "-", word, expands))
                current.append(text[i:m.end()])
                i = m.end()
                continue
        elif c == "\n":
            i += 1
            for strip_tabs, word, expands in pending:
                collected = []
                while i < n:
                    j = text.find("\n", i)
                    raw = text[i:] if j < 0 else text[i:j]
                    i = n if j < 0 else j + 1
                    if (raw.lstrip("\t") if strip_tabs else raw) == word:
                        break
                    collected.append(raw)
                bodies.append(("\n".join(collected), expands))
            pending = []
            if _CONTINUED_RE.search("".join(current)):
                current.append(" ")
                continue
            lines.append(("".join(current), bodies))
            current, bodies = [], []
            continue
        current.append(c)
        i += 1
    if quote:
        raise ValueError("unterminated quote")
    lines.append(("".join(current), bodies))
    return lines


def _substitution_end(line, i):
    """Index just past the `$(...)`, `${...}` or backquoted text that starts at `i`.
    Raises ValueError when it is not closed."""
    if line[i] == "`":
        close = line.find("`", i + 1)
        if close < 0:
            raise ValueError("unterminated substitution")
        return close + 1
    opener = line[i + 1]
    closer = ")" if opener == "(" else "}"
    depth, j, quote = 0, i + 1, None
    while j < len(line):
        c = line[j]
        if quote:
            if c == "\\" and quote == '"':
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            j += 2
            continue
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unterminated substitution")


def _substitutions(line):
    """The inner text of every `$(...)` and backtick substitution a shell would EXECUTE in `line`
    (not inside single quotes; `$((` arithmetic excluded)."""
    found = []
    i, n = 0, len(line)
    in_double = False
    while i < n:
        c = line[i]
        if c == "'" and not in_double:
            close = line.find("'", i + 1)
            i = n if close < 0 else close + 1
            continue
        if c == '"':
            in_double = not in_double
        elif c == "\\":
            i += 2
            continue
        elif c == "`" or (line.startswith("$(", i) and not line.startswith("$((", i)):
            end = _substitution_end(line, i)
            found.append(line[i + 1:end - 1] if c == "`" else line[i + 2:end - 1])
            i = end
            continue
        i += 1
    return found


def _shell_tokens(line):
    """[(text, is_operator, substitutions)] for one command line, with quotes removed as a shell
    removes them.

    shlex cannot do this job: with punctuation_chars it returns a quoted `)` and a real one as
    the same token, and it glues adjacent operators (`);`, `))`) into one, which hid
    `(cd /x); git commit` from the segment splitter. Here an operator is reported only when it
    is unquoted, and `2>` is one redirection. `$(...)`, `${...}` and backquoted text stay inside
    their word, and `substitutions` lists the commands they run, so each is judged in the
    directory of the command it belongs to. Raises ValueError on an unterminated quote or
    substitution."""
    tokens, word, subs, in_word = [], [], [], False
    i, n = 0, len(line)

    def chunk(at):
        end = _substitution_end(line, at)
        word.append(line[at:end])
        subs.extend(_substitutions(line[at:end]))
        return end

    while i < n:
        c = line[i]
        if c in " \t\r\n":
            if in_word:
                tokens.append(("".join(word), False, subs))
                word, subs, in_word = [], [], False
            i += 1
            continue
        if c == "'":
            close = line.find("'", i + 1)
            if close < 0:
                raise ValueError("unterminated quote")
            word.append(line[i + 1:close])
            in_word, i = True, close + 1
            continue
        if c == '"':
            i += 1
            while i < n and line[i] != '"':
                if line[i] == "`" or line.startswith("$(", i):
                    i = chunk(i)
                    continue
                if line[i] == "\\" and i + 1 < n and line[i + 1] in '"\\$`':
                    i += 1
                word.append(line[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated quote")
            in_word, i = True, i + 1
            continue
        if c == "\\":
            word.append(line[i + 1:i + 2])
            in_word, i = True, i + 2
            continue
        if c == "`" or line.startswith(("$(", "${"), i):
            in_word, i = True, chunk(i)
            continue
        op = next((o for o in _OPERATORS if line.startswith(o, i)), None)
        if op is None:
            word.append(c)
            in_word, i = True, i + 1
            continue
        text = "".join(word)
        io_number = text if (in_word and text.isdigit() and op[0] in "<>") else ""
        if in_word and not io_number:
            tokens.append((text, False, subs))
        word, subs, in_word = [], [], False
        tokens.append((io_number + op, True, []))
        i += len(op)
    if in_word:
        tokens.append(("".join(word), False, subs))
    return tokens


def _segments(tokens):
    """Yield (item, op, substitutions, heredocs) per simple command: `item` is its word list
    (redirections included), `op` the operator that ended it (None at the end of the line),
    `substitutions` the commands its words run, and `heredocs` how many `<<` it opens. An
    unquoted `(` or `)` is yielded as the item itself, so the caller can scope a `cd` to its
    subshell."""
    words, subs, heredocs = [], [], 0
    for text, is_op, found in tokens:
        if not is_op or (text not in _SEGMENT_OPS and text not in ("(", ")")):
            words.append(text)
            subs.extend(found)
            if is_op and text.rstrip("-").endswith("<<") and not text.endswith("<<<"):
                heredocs += 1
            continue
        if text in ("(", ")"):
            if words:
                yield words, None, subs, heredocs
            words, subs, heredocs = [], [], 0
            yield text, None, [], 0
            continue
        yield words, text, subs, heredocs
        words, subs, heredocs = [], [], 0
    if words:
        yield words, None, subs, heredocs


def _wrapper_option(words, k, valued):
    """(option name, its value or None, index after it) for the wrapper option at words[k],
    in every spelling getopt accepts: `--opt value`, `--opt=value`, `-o value`, `-ovalue`, and a
    value-taking letter inside a cluster (`-iC dir`)."""
    opt = words[k]
    following = words[k + 1] if k + 1 < len(words) else ""
    if opt.startswith("--"):
        name, eq, value = opt.partition("=")
        if eq:
            return name, value, k + 1
        return (name, following, k + 2) if name in valued else (name, None, k + 1)
    for j in range(1, len(opt)):
        name = "-" + opt[j]
        if name in valued:
            return (name, opt[j + 1:], k + 1) if j + 1 < len(opt) else (name, following, k + 2)
    return opt, None, k + 1


def _strip_prefixes(words, here, home):
    """Drop what precedes the real command word: assignments, keywords, redirections and
    transparent wrappers (`env`, `command`, `nohup`, `timeout N`, `xargs`, `sudo`, ...).
    Returns (words, the directory the command acts on), which applies to this command only:
    - a wrapper's directory option (`env -C`/`--chdir`, `sudo -D`/`--chdir`) moves it;
    - `sudo -i`/`--login` runs it from the target user's home, and a `GIT_DIR=`-style assignment,
      bare or after `env`, points git elsewhere: both make it unknowable;
    - `env -S`/`--split-string` runs its value as a command line, so that value is split into
      words and parsed in place, like the rest of the command."""
    k = 0
    while k < len(words):
        w = words[k]
        name = os.path.basename(w)
        if w == "function" and k + 1 < len(words):
            # Bash permits `function name { ...; }` without parentheses. Inspect
            # its visible body conservatively, as for the name() spelling.
            k += 2
        elif _ASSIGNMENT_RE.match(w) or w in _PREFIX_KEYWORDS:
            if _GIT_ENV_RE.match(w):
                here = None
            k += 1
        elif _REDIR_OP_RE.match(w):
            k += 2
        elif name in _WRAPPERS:
            valued, directory, split = _WRAPPERS[name]
            k += 1
            while k < len(words) and (words[k].startswith("-") or
                                      (name == "env" and _ASSIGNMENT_RE.match(words[k]))):
                opt = words[k]
                if opt == "--":
                    k += 1
                    break
                if not opt.startswith("-"):  # env NAME=value
                    if _GIT_ENV_RE.match(opt):
                        here = None
                    k += 1
                    continue
                if name == "sudo" and (opt == "--login" or
                                       (not opt.startswith("--") and "i" in opt[1:])):
                    here = None
                option, value, k = _wrapper_option(words, k, valued)
                if option in directory:
                    here = _join_target(here, value, home)
                if option in split:
                    try:
                        inserted = shlex.split(value)
                    except ValueError:
                        inserted = value.split()
                    words = words[:k] + inserted + words[k:]
            if name == "timeout" and k < len(words):
                k += 1  # the DURATION operand
        else:
            break
    return words[k:], here


def _join_target(here, value, home):
    """`value` resolved against `here` as a shell and git would, or None when only the shell can
    resolve it: an expansion, a glob, brace expansion, a directory-stack tilde (`~+`, `~-`,
    `~1`), a `~` while `home` is None (see `commit_intent`), or a relative path against an
    unknown directory. `~user` comes from the password database, not HOME, so it resolves."""
    if set(value) & _PATH_SPECIAL:
        return None
    if value.startswith("~"):
        prefix = value[1:].split("/", 1)[0]
        if _DIRECTORY_STACK_TILDE.match(prefix) or (not prefix and home is None):
            return None
        value = os.path.expanduser(value) if prefix else home + value[1:]
    if here is None and not os.path.isabs(value):
        return None
    return os.path.normpath(os.path.join(here or os.sep, value))


def _cd(here, args, home, pushd=False):
    """The directory after `cd`/`pushd` with `args`, or None when the text cannot say. A form
    the shell rejects (an unknown option, more than one operand) fails without moving, and zsh
    reads two operands as a substitution, so neither can be treated as a move. A bare `cd` goes
    to `home`, which is None when HOME may have changed."""
    args = list(args)
    if pushd and (not args or args[0][:1] in "+-"):
        return None  # swaps or rotates the directory stack, or `-n` does not move at all
    while args and args[0].startswith("-") and args[0] != "-":
        opt = args.pop(0)
        if opt == "--":
            break
        if not set(opt[1:]) <= set("LPe@"):
            return None
    if len(args) > 1:
        return None
    if not args:
        return home
    target = args[0]
    if target == "-" or (os.environ.get("CDPATH") and not target.startswith(("/", ".", "~"))):
        return None
    return _join_target(here, target, home)


def _config_key(key):
    """A config key as git compares it: section and variable case-insensitive, subsection not."""
    head, dot, rest = key.partition(".")
    middle, _, last = rest.rpartition(".")
    return head.lower() + dot + (middle + "." if middle else "") + last.lower()


# subcommand -> the no-commit route to offer when it would commit
_COMMIT_PRODUCERS = {
    "commit-tree": "`git commit-tree` writes a commit object the reviewer never sees and has no "
                   "no-commit form; stage the change and run a plain `git commit -m ...`",
    "merge": "`git merge` creates a merge commit the reviewer never sees. Run `git merge "
             "--no-commit --no-ff <branch>` (or `--squash`, or `--ff-only`), resolve, then a "
             "plain `git commit -m ...`",
    "cherry-pick": "`git cherry-pick` commits without review. Run `git cherry-pick -n <commit>`, "
                   "then a plain `git commit -m ...`",
    "revert": "`git revert` commits without review. Run `git revert --no-commit <commit>`, then "
              "a plain `git commit -m ...`",
    "am": "`git am` commits without review. Run `git apply --index <patch>`, then a plain "
          "`git commit -m ...`",
    "pull": "`git pull` can create a merge commit or rebased commits the reviewer never sees. "
            "Run `git pull --ff-only`, `--no-commit` or `--squash` with rebasing off, or "
            "`git fetch` then `git merge --ff-only` (or `--no-commit`/`--squash` and a plain "
            "`git commit -m ...`)",
}
_FALSE_WORDS = {"false", "no", "off", "0", ""}


def _abbreviates(arg, option):
    """git accepts any unambiguous prefix of a long option. Matching a prefix as the option that
    RE-ENABLES a commit errs toward refusing; git itself rejects an ambiguous one."""
    return len(arg) > 2 and option.startswith(arg)


# Options that take a value (so the next word is not a switch), and the long options known to
# take none, per subcommand family. A no-commit switch counts only where it cannot be an option's
# value: after a known flag, an operand, or nothing -- never right after an option this list does
# not know, which might have consumed it.
_MERGE_VALUED_SHORT = set("mFsX")
# `-S[<keyid>]` (merge, pull, cherry-pick, revert): an OPTIONAL value, only ever attached. It
# never takes the next word, and whatever follows it in its cluster is the key id.
_ATTACHED_ONLY_SHORT = set("S")
_MERGE_VALUED_LONG = {"--message", "--file", "--strategy", "--strategy-option", "--cleanup",
                      "--into-name"}
_FETCH_VALUED_SHORT = set("oj")
_FETCH_VALUED_LONG = {"--upload-pack", "--depth", "--deepen", "--shallow-since",
                      "--shallow-exclude", "--refmap", "--server-option", "--jobs",
                      "--negotiation-tip"}
_MERGE_FLAGS = {
    "--commit", "--no-commit", "--squash", "--no-squash", "--ff", "--no-ff", "--ff-only",
    "--edit", "--no-edit", "--stat", "--no-stat", "--summary", "--no-summary", "--log",
    "--no-log", "--verify", "--no-verify", "--signoff", "--no-signoff", "--progress",
    "--no-progress", "--autostash", "--no-autostash", "--allow-unrelated-histories",
    "--rerere-autoupdate", "--no-rerere-autoupdate", "--verbose", "--quiet", "--gpg-sign",
    "--no-gpg-sign", "--verify-signatures", "--no-verify-signatures", "--overwrite-ignore",
    "--no-overwrite-ignore", "--abort", "--quit", "--continue", "--rebase", "--no-rebase",
    "--all", "--append", "--prune", "--tags", "--no-tags", "--force", "--keep", "--unshallow",
    "--update-shallow", "--ipv4", "--ipv6", "--dry-run", "--recurse-submodules",
    "--no-recurse-submodules", "--set-upstream"}
_PICK_VALUED_SHORT = set("mX")
_PICK_VALUED_LONG = {"--mainline", "--strategy", "--strategy-option", "--cleanup"}
_PICK_FLAGS = {
    "--commit", "--no-commit", "--edit", "--no-edit", "--ff", "--allow-empty",
    "--allow-empty-message", "--keep-redundant-commits", "--empty", "--signoff", "--gpg-sign",
    "--no-gpg-sign", "--rerere-autoupdate", "--no-rerere-autoupdate", "--abort", "--quit",
    "--continue", "--skip", "--reference"}


def _options(rest, valued_short, valued_long, known_long):
    """[(name, value, trusted)] for each option in `rest` up to `--`, option VALUES skipped: a
    long option `--name[=value]`, or one entry per letter of a short cluster (`-xn`). `trusted`
    is False for an option straight after an unrecognised long option, which may have taken it
    as its value."""
    found, suspect, k = [], False, 0
    while k < len(rest):
        arg = rest[k]
        k += 1
        if arg == "--":
            break
        if not arg.startswith("-") or arg == "-":
            suspect = False
            continue
        trusted, suspect = not suspect, False
        if arg.startswith("--"):
            name, eq, value = arg.partition("=")
            found.append((name, value if eq else None, trusted))
            if not eq and name in valued_long:
                k += 1
            elif not eq and name not in known_long:
                suspect = True
            continue
        for j, letter in enumerate(arg[1:], 1):
            found.append(("-" + letter, None, trusted))
            if letter in _ATTACHED_ONLY_SHORT:
                break  # `-Skeyid`: the rest of the cluster is its value, never more letters
            if letter in valued_short:
                if j == len(arg) - 1:
                    k += 1  # `-m msg`: the value is the next word; `-mmsg` carried it
                break
    return found


def _merge_options(rest, pull):
    return _options(rest,
                    _MERGE_VALUED_SHORT | (_FETCH_VALUED_SHORT if pull else set()),
                    _MERGE_VALUED_LONG | (_FETCH_VALUED_LONG if pull else set()),
                    _MERGE_FLAGS)


def _merge_state(rest, pull=False):
    """(may_commit, ff_only) for `git merge`/`git pull` with these arguments. Options resolve
    last-wins, as git resolves them: `--commit` undoes `--no-commit`, `--no-squash` undoes
    `--squash`, `--ff`/`--no-ff` undo `--ff-only`. A no-commit switch counts only when spelled in
    full and where it cannot be an option's value; an option that turns committing back on counts
    wherever it appears, abbreviated or not."""
    commit, squash, ff_only = True, False, False
    for name, _, trusted in _merge_options(rest, pull):
        if name in ("--abort", "--quit") and trusted:
            return False, ff_only
        if name == "--no-commit":
            commit = commit and not trusted
        elif name == "--squash":
            squash = squash or trusted
        elif name == "--ff-only":
            ff_only = ff_only or trusted
        elif _abbreviates(name, "--continue"):
            return True, False
        elif _abbreviates(name, "--commit"):
            commit = True
        elif _abbreviates(name, "--no-squash"):
            squash = False
        elif _abbreviates(name, "--no-ff") or _abbreviates(name, "--ff"):
            ff_only = False
    return not (squash or not commit or ff_only), ff_only


def _merge_commits(rest, pull=False):
    """Whether `git merge`/`git pull` with these arguments may create a commit."""
    return _merge_state(rest, pull)[0]


def _picks_commit(rest):
    """Whether `git cherry-pick`/`git revert` with these arguments commits. `-n`/`--no-commit`
    (also inside a short cluster such as `-xn`) stops it, until a later `--commit` -- the
    negation git derives from `--no-commit` -- turns committing back on."""
    commit = True
    for name, _, trusted in _options(rest, _PICK_VALUED_SHORT, _PICK_VALUED_LONG, _PICK_FLAGS):
        if name in ("--abort", "--quit") and trusted:
            return False
        if name in ("--no-commit", "-n"):
            commit = commit and not trusted
        elif _abbreviates(name, "--continue") or name == "--skip":
            return True  # both resume the sequencer, which commits the remaining picks
        elif _abbreviates(name, "--commit"):
            commit = True
    return commit


def _config_value(target, overrides, key):
    """`key` as git would read it for a command in `target`: a `-c` override first, then the
    repository's configuration. Returns the value, None when unset, or False when it cannot be
    read."""
    if key in overrides:
        return overrides[key]
    if target is None or _UNKNOWN_CONFIG in overrides:
        return False
    try:
        p = subprocess.run(["git", "-C", str(target), "config", "--get", key],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if p.returncode == 1:
        return None
    return p.stdout.strip() if p.returncode == 0 else False


def _git_version():
    """(major, minor) of the git on PATH, or (0, 0) when it cannot be read."""
    global _GIT_VERSION
    if _GIT_VERSION is None:
        try:
            out = subprocess.run(["git", "--version"], capture_output=True, text=True,
                                 timeout=5).stdout
            _GIT_VERSION = tuple(int(n) for n in re.findall(r"\d+", out)[:2])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            _GIT_VERSION = (0, 0)
        if len(_GIT_VERSION) != 2:
            _GIT_VERSION = (0, 0)
    return _GIT_VERSION


_GIT_VERSION = None
# From git 2.34, `pull --ff-only` fast-forwards or fails even when pull.rebase is set; before
# that (Ubuntu 20.04 ships 2.25) rebase mode ignored --ff-only and rebased local commits.
_FF_ONLY_BEATS_REBASE = (2, 34)


def _pull_rebases(rest, target, overrides):
    """(rebases, from_config) for `git pull`: the last `--rebase[=…]`/`-r`/`--no-rebase` on the
    command line, else `branch.<current>.rebase`, else `pull.rebase`. Anything it cannot read
    counts as rebasing, so the gate refuses rather than guesses. `--no-rebase` counts only where
    it cannot be an option's value; a rebase switch counts anywhere."""
    rebase = None
    for name, value, trusted in _merge_options(rest, pull=True):
        if name == "--no-rebase":
            rebase = False if trusted else rebase
        elif _abbreviates(name, "--rebase"):
            if value is not None and value.lower() in _FALSE_WORDS:
                rebase = False if trusted else rebase
            else:
                rebase = True
        elif name == "-r":
            rebase = True
    if rebase is not None:
        return rebase, False
    if target is None:
        return True, True
    try:
        p = subprocess.run(["git", "-C", str(target), "symbolic-ref", "--short", "-q", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        branch = p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return True, True
    keys = (["branch.{}.rebase".format(branch)] if branch else []) + ["pull.rebase"]
    for key in keys:
        value = _config_value(target, overrides, key)
        if value is False:
            return True, True
        if value is not None:
            return value.lower() not in _FALSE_WORDS, True
    return False, True


def _pull_commits(rest, target, overrides):
    """Whether `git pull` may create a commit: rebase mode may, unless it came from
    configuration and `--ff-only` overrides it (git >= 2.34); otherwise judge it as a merge."""
    may_commit, ff_only = _merge_state(rest, pull=True)
    rebases, from_config = _pull_rebases(rest, target, overrides)
    if rebases:
        return not (from_config and ff_only and _git_version() >= _FF_ONLY_BEATS_REBASE)
    return may_commit


def _subcommand_intent(sub, rest, target, overrides):
    if sub == "commit":
        return CommitIntent("commit", target, None)
    if sub not in _COMMIT_PRODUCERS:
        return _NO_INTENT
    if sub == "commit-tree":
        commits = True
    elif sub == "merge":
        commits = _merge_commits(rest)
    elif sub == "pull":
        commits = _pull_commits(rest, target, overrides)
    elif sub in ("cherry-pick", "revert"):
        commits = _picks_commit(rest)
    else:  # am
        commits = not any(arg.split("=", 1)[0] in ("--abort", "--quit", "--show-current-patch")
                          for arg in rest)
    return CommitIntent("other", target, _COMMIT_PRODUCERS[sub]) if commits else _NO_INTENT


def _classify_git(args, here, depth, home, overrides=None):
    target = here
    overrides = dict(overrides or {})
    k = 0
    while k < len(args) and args[k].startswith("-"):
        t = args[k]
        name = t.split("=", 1)[0]
        if t == "-C" and k + 1 < len(args):
            target = _join_target(target, args[k + 1], home)
            k += 2
            continue
        if t == "-c" and k + 1 < len(args):
            key, eq, value = args[k + 1].partition("=")
            if _UNRESOLVABLE_C_RE.search(key):
                overrides[_UNKNOWN_CONFIG] = "true"
            else:
                overrides[_config_key(key)] = value if eq else "true"
            k += 2
            continue
        if name in _GIT_LOCATION_OPTS:
            target = None  # another repository, or configuration taken from the environment
        k += 2 if (name in _GIT_GLOBAL_VALUE | _GIT_LOCATION_OPTS and "=" not in t) else 1
    if k >= len(args):
        return _NO_INTENT
    sub, rest = args[k], args[k + 1:]
    if _UNRESOLVABLE_C_RE.search(sub):  # `git $SUBCOMMAND`: any subcommand may run
        return CommitIntent("other", target, _RUNTIME_CODE_REASON)
    if sub not in _GIT_BUILTINS:
        if _UNKNOWN_CONFIG in overrides:
            # An unresolved `-c` may define this alias, before or after any known one.
            return CommitIntent("other", target, _ALIAS_UNKNOWN_REASON.format(sub))
        expansion = overrides.get("alias." + sub.lower())
        if expansion is None:
            if target is None:
                return CommitIntent("other", None, _ALIAS_UNKNOWN_REASON.format(sub))
            expansion = _repo_alias(target, sub)
            if expansion is _ALIAS_UNREADABLE:
                return CommitIntent("other", target, _ALIAS_UNKNOWN_REASON.format(sub))
        if expansion:
            if expansion.startswith("!"):
                script = expansion[1:] + "".join(" " + shlex.quote(a) for a in rest)
                return commit_intent(script, target, depth + 1)
            try:
                words = shlex.split(expansion)
            except ValueError:
                words = []
            if words and depth < _MAX_DEPTH:
                return _classify_git(words + rest, target, depth + 1, home, overrides)
    return _subcommand_intent(sub, rest, target, overrides)


_ALIAS_UNREADABLE = object()


def _repo_alias(target, name):
    """`alias.<name>` as git itself resolves it (includes and all config scopes), None when it
    is not set, or `_ALIAS_UNREADABLE` when git could not be asked. A directory that does not
    exist is "not set": git cannot run there either, so nothing commits."""
    if not os.path.isdir(str(target)):
        return None
    try:
        p = subprocess.run(["git", "-C", str(target), "config", "--get", "alias." + name],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return _ALIAS_UNREADABLE
    if p.returncode == 1:
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else _ALIAS_UNREADABLE


def _runs_external(words):
    """Whether the command word is reached through a wrapper that runs a PROGRAM (`env`,
    `sudo`, `nohup`, ...): a shell builtin such as `cd` cannot run that way. `command` and
    `builtin` do run builtins."""
    k = 0
    while k < len(words):
        w = words[k]
        if _ASSIGNMENT_RE.match(w) or w in _PREFIX_KEYWORDS:
            k += 1
        elif _REDIR_OP_RE.match(w):
            k += 2
        else:
            name = os.path.basename(w)
            return name in _WRAPPERS and name not in ("command", "builtin")
    return False


def _expand_known(word, known):
    """`word` with each `$name`/`${name}` whose value this call assigned literally replaced
    by that value. Anything else is left as written, so it still reads as an expansion."""
    def value(match):
        name = match.group(1) or match.group(2)
        found = known.get(name)
        return found if found is not None else match.group(0)
    return _EXPANSION_RE.sub(value, word)


def _expand_segment(words, known):
    """`words` with this call's literal assignments substituted (see `_expand_known`), split the
    way a shell splits an unquoted expansion where that matters: the command word (`$cmd` holding
    `git commit -m x`), and every argument of a `git` command (`git $opts commit` with
    opts='-C /tmp'). A quoted expansion reaches git as one word, which git rejects, so splitting
    there is right or harmless; other commands keep their words whole, so `bash -c "$cmd"` still
    hands the shell the whole script."""
    expanded = [(_expand_known(w, known), w) for w in words]
    first, original = expanded[0]
    head = first.split() if first != original and len(first.split()) > 1 else [first]
    flat = head + [new for new, _ in expanded[1:]]
    bare = _strip_prefixes(flat, None, None)[0]
    if not (bare and os.path.basename(bare[0]) == "git"):
        return flat
    return head + [part for new, old in expanded[1:]
                   for part in (new.split() if new != old else [new])]


def _record_assignments(words, known, certain):
    """Remember `name=value` from a command that only assigns (bare, or after `export`,
    `declare`, ...), so a later `eval "$cmd"` or `$cmd` can be judged by what it runs. A value
    that is itself an expansion, or an assignment that may not have run or may not persist
    (`certain` False: after `&&`/`||`, in a pipeline, subshell or compound command), is
    recorded as unknown (None)."""
    if not words:
        return
    rest = words[1:] if words[0] in _DECLARERS else words
    if not rest or not all(_ASSIGNMENT_RE.match(w) or w.startswith("-") for w in rest):
        return
    for w in rest:
        if _ASSIGNMENT_RE.match(w):
            name, _, value = w.partition("=")
            known[name] = value if certain and not _UNRESOLVABLE_C_RE.search(value) else None


def _classify_segment(words, here, body, depth, home):
    """(CommitIntent, directory after this segment) for one simple command. Only `cd`, `pushd`,
    `popd` and an `eval` that runs one of them move the directory; a prefix such as `env -C` or
    `GIT_DIR=` applies to its own command, and a nested shell cannot move its parent. A `HOME=`
    prefix changes what a bare `cd` or `cd ~` reaches, so `~` is not resolved."""
    if any(w.startswith("HOME=") for w in words):
        home = None
    external = _runs_external(words)
    words, inner = _strip_prefixes(words, here, home)
    if not words:
        return _NO_INTENT, here
    head = words[0]
    name = os.path.basename(head)
    if head in ("cd", "pushd", "popd"):
        if external:  # `env cd /x` runs a program, not the builtin: the shell does not move
            return _NO_INTENT, here
        if head == "popd":
            return _NO_INTENT, None
        return _NO_INTENT, _cd(inner, words[1:], home, pushd=head == "pushd")
    if _UNRESOLVABLE_C_RE.search(head):
        # The command word is an expansion this call did not assign. Refused only when the
        # visible text names a commit producer (`$GIT_BIN commit -m x`). Otherwise
        # (`"$PYTHON" -m pytest`, `eval "$(ssh-agent -s)"`) it is not treated as a commit: a
        # commit held entirely in a variable set outside the call is a documented limit, like a
        # script file, and refusing every such command broke everyday work (Task C2.2). The
        # expansion may also be a shell or interpreter (`"$PYTHON" -c '...'`, `$SHELL <<EOF`),
        # so code the text does show -- an argument or a heredoc body mentioning git and a
        # producer -- is refused too.
        # Data that only mentions a commit (`"$PYTHON" -c 'print("git commit")'`) is not code:
        # a mention counts only with a spawn primitive of some interpreter, or when the text is
        # itself a shell command line that commits.
        if any(w in _PRODUCER_WORDS for w in words[1:]):
            return CommitIntent("other", inner, _UNRESOLVABLE_HEAD_REASON), here
        for code in words[1:] + [body or ""]:
            if _INLINE_GIT_COMMIT_RE.search(code) and (
                    _ANY_SPAWN_RE.search(code)
                    or commit_intent(code, inner, depth + 1).kind is not None):
                return CommitIntent("other", inner, _RUNTIME_CODE_REASON), here
        return _NO_INTENT, here
    here, outer = inner, here  # everything below acts on `inner` and leaves `outer` in place
    if name == "git":
        return _classify_git(words[1:], here, depth, home), outer
    if name == "eval":
        script = " ".join(words[1:])
        moves = (re.search(r"(?:\A|[;&|(\s])(?:cd|pushd|popd)\b", script)
                 or _UNRESOLVABLE_C_RE.search(script))  # expanded code may move it too
        return commit_intent(script, here, depth + 1), (None if moves else outer)
    if name in _SHELLS:
        rest = words[1:]
        if body is None and "<<<" in rest[:-1]:
            body = rest[rest.index("<<<") + 1]  # a here-string is a script too
        index = 0
        while index < len(rest):
            word = rest[index]
            if _REDIR_OP_RE.match(word):  # `bash <<'EOF'`: `<<` and its word are not operands
                index += 2
                continue
            if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
                script = rest[index + 1] if index + 1 < len(rest) else ""
                return commit_intent(script, here, depth + 1), outer
            if not word.startswith("-"):
                return _NO_INTENT, outer  # a script FILE: a documented gap
            index += 1
        if body is not None:  # `bash <<EOF` / `sh -s <<EOF`: the body IS the script
            return commit_intent(body, here, depth + 1), outer
        return _NO_INTENT, outer
    if _INTERPRETER_RE.match(name):
        code = ""
        for index, word in enumerate(words[1:], 1):
            if word in ("-c", "-e", "--eval") and index + 1 < len(words):
                code = words[index + 1]
                break
        if not code and body is not None and "-" in words[1:]:
            code = body
        # A mention of a commit counts only in a script that can run a command (Task C2.3):
        # `print('run git commit')`, or a report quoting it, is data.
        family = "python" if name.startswith("python") else name
        if (code and _INLINE_GIT_COMMIT_RE.search(code)
                and re.search(_INLINE_SPAWN[family], code)):
            return CommitIntent("other", here, _INLINE_REASON), outer
    return _NO_INTENT, outer


def _settle(here, start, late):
    """The directory after an and-or list that began in `start`. A cd that ran unconditionally
    (first in the list) into a directory this process can search is certain; any other change
    is not. `isdir` alone is not enough: `cd` fails on a directory without search permission."""
    if here == start:
        return here
    if late or here is None or not (os.path.isdir(here) and os.access(here, os.X_OK)):
        return None
    return here


def _regex_fallback(text, target):
    if _COMMIT_RE.search(text or ""):
        return CommitIntent("commit", target, None)
    return _NO_INTENT


def commit_intent(command, cwd, _depth=0, _known=None):
    """The first commit-producing git invocation `command` would run, as a CommitIntent.

    Looks only at real command positions, so text inside quoted arguments, comments and data
    here-documents never counts, while `$(...)`/backtick substitutions (which a shell executes)
    do. Tracks `cd`, so the target is the directory the commit would act on -- or None when the
    shell may not have changed directory as written (see `_settle` and the subshell handling
    below). Any lexing failure falls back to `_COMMIT_RE` over the raw text -- the conservative
    old detector.

    HOME is trusted (for `~` and a bare `cd`) only in the FIRST command of a top-level call:
    nothing has run before it that could change HOME, which a later command cannot rely on
    (`export HOME=…`, `declare "HO""ME=…"`, a nested shell started with another HOME, ...).
    Elsewhere a `~` target is unknowable. The gate's own plain `git -C ~/repo commit` is always
    a first command, so it keeps working."""
    here = str(cwd) if cwd is not None else None
    ran = False
    if _depth > _MAX_DEPTH:
        return CommitIntent("other", here, "the command nests shells too deeply for the gate "
                                           "to follow; run git directly")
    try:
        lines = _split_script(command)
    except ValueError:
        return _regex_fallback(command, here)
    stack, compound = [], 0
    known = dict(_known or {})  # variables this call assigned literally (see _expand_known)
    for line, heredocs in lines:
        try:
            tokens = _shell_tokens(line)
        except ValueError:
            found = _regex_fallback(line, here)
            if found.kind:
                return found
            continue
        # `start`: the directory the current and-or list began in; `pure`: every operator so far
        # in it is `&&`; `late`: a cd in it ran only after another command succeeded.
        start, pure, late, prev = here, True, False, None
        upstream = []  # what earlier elements of the current pipeline carry (words, bodies)
        for item, op, subs, opened in _segments(tokens):
            if item == "(":  # a subshell: nothing inside can move the outer directory
                stack.append((here, start, pure, late))
                start, pure, late, prev = here, True, False, None
                continue
            if item == ")":
                if stack:
                    here, start, pure, late = stack.pop()
                continue
            if prev == "||":
                pure = False
            first = item[0] if item else None
            if first in _COMPOUND_OPEN:
                compound += 1
            # This command's here-documents, in `<<` order. An unquoted delimiter's body runs
            # its `$(...)` like the command's own words; all of them run in THIS command's
            # directory, before it starts.
            mine, heredocs = heredocs[:opened], heredocs[opened:]
            body = "\n".join(text for text, _ in mine) if mine else None
            for text, expands in mine:
                if expands:
                    try:
                        subs = subs + _substitutions(text)
                    except ValueError:
                        found = _regex_fallback(text, here)
                        if found.kind:
                            return found
            for inner in subs:
                found = commit_intent(inner, here, _depth + 1, known)
                if found.kind:
                    return found
            if prev not in _PIPES:
                upstream = []
            elif item and body is None:
                bare = _strip_prefixes(item, here, None)[0]
                if bare and os.path.basename(bare[0]) in _SHELLS:
                    # `... | bash`: the shell runs whatever the pipeline feeds it, which is only
                    # knowable from the text upstream. Judge it conservatively, as the regex
                    # fallback does: a mention of a commit there counts.
                    text = "\n".join(upstream)
                    # Pipeline output can name an absolute/quoted Git executable or
                    # another producer. Its target need not be this shell's cwd.
                    found = _regex_fallback(text, None)
                    if not found.kind and _PIPELINE_GIT_RE.search(text):
                        found = CommitIntent("other", None, _INLINE_REASON)
                    if found.kind:
                        return found
            if item:
                item = _expand_segment(item, known)
                upstream = upstream + [" ".join(item)] + [text for text, _ in mine]
                # Not expanduser: with HOME unset it falls back to the passwd home, while a bare
                # `cd` in bash fails and stays put. Unset or empty HOME is unknown.
                home = (os.environ.get("HOME") or None) if (_depth == 0 and not ran) else None
                ran = True
                found, after = _classify_segment(item, here, body, _depth, home)
                if found.kind:
                    return found
                _record_assignments(item, known, certain=(
                    prev in _LIST_ENDS and op not in _PIPES and op not in ("&", "||", "&&")
                    and not compound and not stack))
                if after != here and not (prev in _PIPES or op in _PIPES or op == "&"):
                    # A pipeline element or background job runs in a subshell: no change.
                    if compound or op == "||" or not (prev in _LIST_ENDS or pure):
                        after = None
                    late = late or prev not in _LIST_ENDS
                    here = after
            if first in _COMPOUND_CLOSE:
                compound = max(0, compound - 1)
            if op in _LIST_ENDS:
                here = start if op == "&" else _settle(here, start, late)
                start, pure, late = here, True, False
            prev = op
        here = _settle(here, start, late)  # a newline ends the list
    return _NO_INTENT


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


def commit_confirmation_command(published):
    """The harmless shell command the gate substitutes (via the PreToolUse `updatedInput` field)
    for the agent's `git commit` AFTER the gate has performed the commit ITSELF. A single `echo`
    that names the published id: the intercepted tool call then succeeds visibly -- a normal, green
    result -- instead of being denied and rendered as an error, and because it never runs git it
    cannot re-commit and does not re-enter the gate. `shlex.quote` keeps the argument safe even
    though the id is always a plain hash (or a fixed fallback)."""
    sha = published or "(id unavailable)"
    return "echo " + shlex.quote(
        "[reviewerAgent] committed the reviewed staged set as {} -- the gate performed the "
        "commit; nothing further to do.".format(sha)
    )


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
