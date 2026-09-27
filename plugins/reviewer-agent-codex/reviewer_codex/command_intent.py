# SPDX-License-Identifier: MPL-2.0
"""Shell commit detection for the independently distributed Codex package.

Ported from the Claude package's command-position classifier. Keep its parser
regressions here too: this package cannot import the repository-root runtime.
This detects visible commands, not arbitrary programs hidden in script files.
"""
import os
import re
import shlex
import subprocess
from collections import namedtuple

_COMMIT_RE = re.compile(r"(?:^|[\s;&|()])git\s+(?:[^\s;&|]+\s+)*commit(?=$|[\s;&|])")


_GIT_GLOBAL_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
    "--super-prefix",
}


_SHELL_OPS = {"&&", "||", "|", "|&", ";", "&"}


_UNRESOLVABLE_C_RE = re.compile(r"[$`]")


_DIRECTORY_STACK_TILDE = re.compile(r"\A(?:[+-][0-9]*|[0-9]+)\Z")


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


CommitIntent = namedtuple("CommitIntent", "kind target reason")


_NO_INTENT = CommitIntent(None, None, None)


_MAX_DEPTH = 4


_OPERATORS = ("<<<", "<<-", "&>>", "&&", "||", ";;", "|&", "<<", ">>", "<&", ">&", "&>", "<>",
              ">|", "(", ")", ";", "|", "&", "<", ">")


_SEGMENT_OPS = _SHELL_OPS | {";;"}


_LIST_ENDS = {None, ";", ";;", "&"}


_PIPES = {"|", "|&"}


_COMPOUND_OPEN = {"if", "while", "until", "for", "case", "select", "{", "function"}


_COMPOUND_CLOSE = {"fi", "done", "esac", "}"}


_ASSIGNMENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")


_GIT_ENV_RE = re.compile(r"\AGIT_(?:DIR|WORK_TREE|COMMON_DIR|CONFIG[A-Z0-9_]*)=")


_GIT_LOCATION_OPTS = {"--git-dir", "--work-tree", "--config-env"}


_PREFIX_KEYWORDS = {"!", "{", "then", "do", "else", "elif", "if", "while", "until", "time"}


_REDIR_OP_RE = re.compile(r"\A[0-9]*(?:[<>]+[&|-]?|&>>?)\Z")


_HEREDOC_RE = re.compile(
    r'''<<(-?)[ \t]*((?:[^\s;&|<>()'"\\]+|'[^']*'|"(?:\\.|[^"\\])*"|\\[^\n])+)'''
)


_PATH_SPECIAL = set("$`*?[]{}")


_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}


_INTERPRETER_RE = re.compile(r"\A(?:python[0-9.]*|node|perl|ruby)\Z")


_INLINE_GIT_COMMIT_RE = re.compile(r"\bgit\b[\s\S]*?\b(?:commit|commit-tree|merge|cherry-pick|revert|am|pull)\b")

_PIPELINE_GIT_RE = re.compile(r"\bgit\b[\s\S]*?\b(?:commit|commit-tree|merge|cherry-pick|revert|am|pull)\b")

# Bounded interpreter inspection: text alone does not execute a command. A script
# mentioning a producer AND a spawning primitive remains conservatively refused.
_INLINE_SPAWN_RE = {
    "python": re.compile(r"\b(?:subprocess|system|popen|exec[lv]p?e?|spawn[lv]?p?e?|posix_spawnp?|pty)\b"),
    "node": re.compile(r"\b(?:child_process|exec|execSync|execFile|execFileSync|spawn|spawnSync|fork)\b"),
    "perl": re.compile(r"\b(?:system|exec|qx)\b|`|\bopen\b[^;\n]*\|"),
    "ruby": re.compile(r"\b(?:system|exec|spawn|popen\w*|Open3|capture\w*)\b|`|%x"),
}

_ANY_SPAWN_RE = re.compile("|".join("(?:{})".format(p.pattern) for p in _INLINE_SPAWN_RE.values()))


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


_MERGE_VALUED_SHORT = set("mFsX")


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


def _classify_git(args, here, depth, home, overrides=None, accept=None):
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
                return commit_intent(script, target, depth + 1, _accept=accept)
            try:
                words = shlex.split(expansion)
            except ValueError:
                words = []
            if words and depth < _MAX_DEPTH:
                return _classify_git(words + rest, target, depth + 1, home, overrides, accept)
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


def _classify_segment(words, here, body, depth, home, accept=None):
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
        if any(w in _PRODUCER_WORDS for w in words[1:]):
            return CommitIntent("other", inner, _UNRESOLVABLE_HEAD_REASON), here
        # The unresolved name may be an interpreter writing a report. As in the
        # Claude copy, require a spawn primitive or executable shell code too.
        for code in words[1:] + [body or ""]:
            if _INLINE_GIT_COMMIT_RE.search(code) and (
                    _ANY_SPAWN_RE.search(code)
                    or commit_intent(code, inner, depth + 1, _accept=accept).kind is not None):
                return CommitIntent("other", inner, _RUNTIME_CODE_REASON), here
        return _NO_INTENT, here
    here, outer = inner, here  # everything below acts on `inner` and leaves `outer` in place
    if name == "git":
        return _classify_git(words[1:], here, depth, home, accept=accept), outer
    if name == "eval":
        script = " ".join(words[1:])
        moves = (re.search(r"(?:\A|[;&|(\s])(?:cd|pushd|popd)\b", script)
                 or _UNRESOLVABLE_C_RE.search(script))  # expanded code may move it too
        return commit_intent(script, here, depth + 1, _accept=accept), (None if moves else outer)
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
                return commit_intent(script, here, depth + 1, _accept=accept), outer
            if not word.startswith("-"):
                return _NO_INTENT, outer  # a script FILE: a documented gap
            index += 1
        if body is not None:  # `bash <<EOF` / `sh -s <<EOF`: the body IS the script
            return commit_intent(body, here, depth + 1, _accept=accept), outer
        return _NO_INTENT, outer
    if _INTERPRETER_RE.match(name):
        code = ""
        for index, word in enumerate(words[1:], 1):
            if word in ("-c", "-e", "--eval") and index + 1 < len(words):
                code = words[index + 1]
                break
        if not code and body is not None and "-" in words[1:]:
            code = body
        family = "python" if name.startswith("python") else name
        if code and _INLINE_GIT_COMMIT_RE.search(code) and _INLINE_SPAWN_RE[family].search(code):
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


def commit_intent(command, cwd, _depth=0, _known=None, _accept=None):
    """The first commit-producing git invocation `command` would run, as a CommitIntent.

    An optional acceptance predicate lets the gate skip inactive targets without
    hiding a later protected commit in the same call, including nested shells.
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
            if found.kind and (_accept is None or _accept(found)):
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
                        if found.kind and (_accept is None or _accept(found)):
                            return found
            for inner in subs:
                found = commit_intent(inner, here, _depth + 1, known, _accept)
                if found.kind and (_accept is None or _accept(found)):
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
                    if found.kind and (_accept is None or _accept(found)):
                        return found
            if item:
                item = _expand_segment(item, known)
                upstream = upstream + [" ".join(item)] + [text for text, _ in mine]
                # Not expanduser: with HOME unset it falls back to the passwd home, while a bare
                # `cd` in bash fails and stays put. Unset or empty HOME is unknown.
                home = (os.environ.get("HOME") or None) if (
                    _depth == 0 and not ran and cwd is not None
                ) else None
                ran = True
                found, after = _classify_segment(item, here, body, _depth, home, _accept)
                if found.kind and (_accept is None or _accept(found)):
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
