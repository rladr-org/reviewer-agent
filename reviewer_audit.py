#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Per-audit records: what a review cost, what it was given, and what it actually did.

Four things the gate could not answer before this module existed:

1. Did the reviewer run the delta command Phase C hands it, or re-read whole files anyway?
   codex echoes every shell command to stderr, `_run` captures all of it, and the log then kept
   300 characters and threw the rest away. The counters here are derived BEFORE that truncation.
2. How long did the review take? Nothing recorded a duration.
3. How large was the staged input -- the dominant cost driver, and the only thing a token count
   can be normalized against.
4. Which model produced the verdict? 2 of 1832 logged reviews recorded one.

One append-only JSONL per project, one line per ATTEMPT including timeouts, crashes and empty
verdicts. That is deliberately NOT the rule used for the saved verdict: writing the verdict file
DESTROYS the previous findings, so it is gated on a completed audit, whereas appending destroys
nothing and an incomplete attempt is a first-class outcome. A reviewer that times out is a worse
reviewer, and dropping those rows would make every model look equally reliable.

An ATTEMPT means the reviewer was actually asked. The gate's earlier refusals -- a commit
reaching beyond the index, an empty stage, a session that never started -- are recorded in the
log but not here: no model was given a prompt, so there is nothing to re-issue and no cost to
compare. A record must be built BEFORE the caller writes the saved verdict, because the capsule
names that file only when it already exists.

Nothing here may raise into a caller. The commit gate is fail-CLOSED, so an exception in
instrumentation would block a commit that the reviewer approved.

Schema and rationale: REVIEW_COST_REDUCTION_PLAN.md, Phase A. This module imports
`reviewer_lib`, never the reverse.
"""
import datetime
import hashlib
import json
import re
import shlex
import subprocess

import reviewer_lib as rl

# ANSI SGR styling. Every parser below reads STRIPPED text: codex writes to a terminal stream and
# styles the token footer, and a parser that does not expect the escape silently drops a fifth of
# all rows while looking like a clean parse. Measured on the real log, 2026-07-30.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# The footer, ANCHORED to whole lines. "tokens used" is also an English phrase reviewers write in
# their findings, and a loose search for it over-counts by about 25%.
_FOOTER = re.compile(r"^tokens used[ \t]*\r?\n[ \t]*([\d,]+)[ \t]*$", re.M)

# codex's echo of a shell command it ran:
#     exec\n<command line>\n succeeded in <N>ms:
#     exec\n<command line>\n exited 1 in <N>ms:      <- a nonzero exit, seen in real stderr
# The outcome alternatives must be complete. The command is matched non-greedily, so an outcome
# line the pattern does not recognise does not end the entry -- it runs on to the NEXT command's
# outcome and silently merges two commands into one.
_EXEC = re.compile(
    r"^exec[ \t]*\r?\n(.*?)\r?\n[ \t]*(succeeded|failed|exited\s+-?\d+) in (\d+)ms",
    re.M | re.S)

# The `git diff <blob-sha> :<path>` that Phase C emits. `git diff --cached` is excluded by
# construction: it is the whole staged patch, so counting it here would make the Phase C
# measurement always look successful. (An earlier version of this comment justified the exclusion
# with "which EVERY audit reads". That premise is FALSE -- measured 2026-08-02 over 750 recorded
# audits, a minority of approvals show no staged read by any route. The exclusion is still right;
# only its stated reason was. `_STAGED_READ` below now answers that question directly.)
_DELTA_DIFF = re.compile(r"git diff\s+[0-9a-f]{40}\s")

# Did the reviewer read the CHANGE UNDER REVIEW? Its own counter, because neither of the two
# below can answer that: `_DELTA_DIFF` deliberately excludes the staged patch, and `_FULL_READ`
# lists whole-file readers, of which `git diff --cached` is not one. Reading a zero from either
# as "saw nothing" is a category error, and it is the one that overstated the unevidenced-approval
# rate by roughly threefold.
#
# Parse shell words rather than searching raw text: reviewer commands routinely use `rg` or
# `echo` to mention a command, and a mention is not evidence that Git showed the staged bytes.
_SHELLS = {"bash", "dash", "ksh", "sh", "zsh"}
_SHELL_SEPARATORS = frozenset(";&|()")
_SHELL_PREFIXES = {"!", "do", "if", "then", "while", "until", "{"}
_COMMAND_PREFIXES = {"builtin", "command", "exec", "nohup", "time"}
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_GIT_GLOBAL_WITH_VALUE = {
    "-C", "-c", "--exec-path", "--git-dir", "--namespace", "--work-tree",
}

# Reading a whole file rather than a delta. Not exhaustive, and not meant to be: it measures the
# direction of a trend, so a command it misses costs a data point, not a wrong answer.
_FULL_READ = ("git show ", "cat ", "sed -n", "rg ", "grep ", "head ", "tail ", "less ")
_TEST_RUN = ("pytest", "unittest", "npm test", "yarn test", "cargo test", "go test")

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")


def strip_ansi(text):
    return _ANSI.sub("", text or "")


def _basename(command):
    return command.rsplit("/", 1)[-1]


def _shell_words(command):
    """Return executable shell words from Codex's command echo, or an empty list on bad syntax.

    Codex normally reports `/bin/bash -lc '<script>' in <cwd>`. Unwrapping that known runner
    before tokenizing also discards the reporting-only `in <cwd>` suffix. The second lexer emits
    control operators as tokens so every pipeline/list member can be considered independently.
    """
    script = command
    try:
        outer = shlex.split(command)
    except ValueError:
        return []
    if outer and _basename(outer[0]) in _SHELLS:
        for i, word in enumerate(outer[1:], 1):
            if word.startswith("-") and "c" in word[1:] and i + 1 < len(outer):
                script = outer[i + 1]
                break
    try:
        lexer = shlex.shlex(script, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return []


def _git_segment_reads_staged(words, depth=0):
    """Whether one simple shell-command segment executes `git diff --cached/--staged`."""
    words = list(words)
    while words and (words[0] in _SHELL_PREFIXES or _ASSIGNMENT.match(words[0])):
        words.pop(0)
    while words and words[0] in _COMMAND_PREFIXES:
        words.pop(0)
        while words and words[0].startswith("-"):
            words.pop(0)
        while words and _ASSIGNMENT.match(words[0]):
            words.pop(0)
    if not words:
        return False

    executable = _basename(words[0])
    if executable in _SHELLS and depth < 2:
        for i, word in enumerate(words[1:], 1):
            if word.startswith("-") and "c" in word[1:] and i + 1 < len(words):
                return _command_reads_staged(words[i + 1], depth=depth + 1)
        return False
    if executable != "git":
        return False

    i = 1
    while i < len(words) and words[i] != "diff":
        option = words[i]
        if option == "--" or not option.startswith("-"):
            return False
        if option in _GIT_GLOBAL_WITH_VALUE:
            i += 2
        else:
            i += 1
    if i >= len(words):
        return False
    for argument in words[i + 1:]:
        if argument == "--":
            break
        if argument in {"--cached", "--staged"}:
            return True
    return False


def _command_reads_staged(command, depth=0):
    """Whether command text contains an actual successful staged-diff invocation."""
    segment = []
    for word in _shell_words(command):
        if word and all(char in _SHELL_SEPARATORS for char in word):
            if _git_segment_reads_staged(segment, depth=depth):
                return True
            segment = []
        else:
            segment.append(word)
    return _git_segment_reads_staged(segment, depth=depth)


# --------------------------------------------------------------------------
# parsing what codex reported
# --------------------------------------------------------------------------
# A required fix opens its own line as "<n>." or "<n>)". Anchored to the line start (allowing a
# little indent) because findings wrap freely and routinely contain dotted text mid-line -- dates
# like 2026-08-01, versions like 3.11, `file.py:20` citations -- none of which start an item.
_REQUIRED_FIX_ITEM = re.compile(r"^[ \t]{0,3}\d+[.)][ \t]", re.MULTILINE)


def count_required_fixes(out, completed, approved):
    """How many numbered REQUIRED fixes this verdict demanded.

    `None` when the question does not apply -- a timeout, crash or empty response produced no
    verdict at all. Deliberately not 0 there: 0 means "demanded nothing", which is true of an
    APPROVAL and false of a review that never returned one, and conflating them would drag any
    average of this field down with rows that measured nothing.

    SUGGESTIONS are excluded via the same splitter the relay uses, so the count is exactly what
    BLOCKED the commit. Counting them would overstate the demand, which is the opposite of why
    that block exists.

    Recorded at attempt time because it cannot be recovered later: `verdict_file` is a
    per-session path that subsequent reviews overwrite, so historically 166 rejections pointed at
    6 surviving files."""
    if not completed:
        return None
    if approved:
        return 0
    required, _ = rl._split_suggestions(out)
    return len(_REQUIRED_FIX_ITEM.findall(required or ""))


def parse_codex_tokens(stream):
    """The cumulative token total from codex's footer, or None.

    CUMULATIVE per reviewer session (25,276,912 -> 25,346,444 -> 25,385,151 across three
    consecutive audits), so a per-call cost is a delta within one session -- see
    `previous_token_total`. The LAST footer wins, since prose can precede a real one."""
    hits = _FOOTER.findall(strip_ansi(stream))
    if not hits:
        return None
    try:
        return int(hits[-1].replace(",", ""))
    except ValueError:
        return None


def parse_exec_trace(stream):
    """[{cmd, ms, ok, code}] for every shell command codex echoed, in order.

    The command LINES are kept, never their output: the commands are our own git and test
    invocations, whereas the output is arbitrary repository content.

    `code` is the NUMERIC exit status -- 0 for "succeeded", N for "exited N", and None for
    codex's bare "failed in", which reports no number. It is kept alongside `ok` rather than
    replacing it because the boolean answers a different question: exit 1 from pytest means the
    suite ran and something failed, exit 127 means it was never there, and both are `ok=False`.
    Only the number separates "the reviewer checked and disagrees" from "the reviewer could not
    check", which is the distinction a verdict citing a test count turns on."""
    out = []
    for m in _EXEC.finditer(strip_ansi(stream)):
        try:
            ms = int(m.group(3))
        except ValueError:
            continue
        outcome = m.group(2).strip()
        if outcome == "succeeded":
            code = 0
        elif outcome.startswith("exited"):
            try:
                code = int(outcome.split()[1])
            except (IndexError, ValueError):   # pragma: no cover -- the regex pins the digits
                code = None
        else:
            code = None
        out.append({"cmd": m.group(1).strip(), "ms": ms,
                    "ok": outcome == "succeeded", "code": code})
    return out


def exec_counters(trace):
    """Counters over a parsed trace. Categories are independent, not a partition: a command can
    read a file and run a test, and forcing a single bucket would hide one of them."""
    counters = {
        "exec_count": 0, "exec_ms_total": 0, "exec_delta_diffs": 0,
        "exec_full_reads": 0, "exec_test_runs": 0,
        "exec_failed": 0, "exec_test_runs_failed": 0, "exec_staged_reads": 0,
    }
    for entry in trace or []:
        cmd = str(entry.get("cmd") or "")
        counters["exec_count"] += 1
        try:
            counters["exec_ms_total"] += int(entry.get("ms") or 0)
        except (TypeError, ValueError):
            pass
        # An outcome we could not parse counts as FAILED, not as succeeded: a command whose
        # result is unreadable has established nothing, which is the side to err on when the
        # number is used to decide whether a finding may block.
        failed = entry.get("code") != 0
        if failed:
            counters["exec_failed"] += 1
        if _DELTA_DIFF.search(cmd):
            counters["exec_delta_diffs"] += 1
        # SUCCESSES only, deliberately breaking the attempts-plus-_failed convention the test
        # counters use. This one is asked whether the reviewer SAW the change, and a command that
        # exited nonzero showed it nothing -- so counting the attempt would assert the very thing
        # the failure disproves.
        if not failed and _command_reads_staged(cmd):
            counters["exec_staged_reads"] += 1
        # Separate `if`s, not a chain: one shell line can re-read files AND run tests, and
        # collapsing it into a single bucket would undercount exactly the number being tracked.
        if any(marker in cmd for marker in _TEST_RUN):
            counters["exec_test_runs"] += 1
            if failed:
                counters["exec_test_runs_failed"] += 1
        if any(marker in cmd for marker in _FULL_READ):
            counters["exec_full_reads"] += 1
    return counters


# --------------------------------------------------------------------------
# staged input
# --------------------------------------------------------------------------
def _git(proj, args):
    try:
        p = subprocess.run(["git", "-C", str(proj)] + args, capture_output=True, text=True)
    except (OSError, ValueError):
        return None
    return p.stdout if p.returncode == 0 else None


def staged_size(proj):
    """(files, added+deleted lines) for the staged set, or (0, 0) outside a repository.

    Binary files report "-" for both counts in `--numstat`; they count as a file and contribute
    no lines, rather than raising on int("-")."""
    text = _git(proj, ["diff", "--cached", "--numstat"])
    if not text:
        return (0, 0)
    files = lines = 0
    for row in text.splitlines():
        parts = row.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        for n in parts[:2]:
            if n.isdigit():
                lines += int(n)
    return (files, lines)


def _head(proj):
    return (_git(proj, ["rev-parse", "HEAD"]) or "").strip()


# --------------------------------------------------------------------------
# content-addressed prompt parts
# --------------------------------------------------------------------------
def prompt_parts_dir():
    return rl.reviewer_home() / "reviews" / "prompt_parts"


def sha256_text(text):
    return hashlib.sha256((text or "").encode("utf-8", "surrogateescape")).hexdigest()


def store_prompt_part(text):
    """Store one prompt component by content and return its sha256.

    A hash alone does NOT make a part recoverable, which is why the bytes are kept:
    `reviewerAgent.md` is read from the WORKING TREE and nothing requires it to be tracked, and
    the live install at ~/.claude/reviewerAgent is not a git checkout, so there is no commit to
    resolve `COMMIT_PROMPT` or the capsule from. Deduplication makes it nearly free -- a few
    distinct briefs across a whole corpus, not one copy per audit."""
    body = text or ""
    sha = sha256_text(body)
    p = prompt_parts_dir() / (sha + ".txt")
    try:
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8", errors="surrogateescape")
    except OSError:
        pass
    return sha


def load_prompt_part(sha):
    if not _SHA256.match(str(sha or "")):
        return None
    try:
        return (prompt_parts_dir() / (str(sha) + ".txt")).read_text(
            encoding="utf-8", errors="surrogateescape")
    except OSError:
        return None


def redact_argv(argv, prompt):
    """The executed argv with the prompt element replaced by its hash. The prompt is large and
    carries operator prose; `static` and `assembled_sha256` already cover it."""
    body = prompt or ""
    if not body:
        return [str(a) for a in argv or []]
    marker = "sha256:" + sha256_text(body)
    return [marker if str(a) == body else str(a) for a in argv or []]


# --------------------------------------------------------------------------
# the log
# --------------------------------------------------------------------------
def audit_log_file(proj):
    # `rl._state_stem` is the one private helper this module reaches for, deliberately: the
    # audit log has to sit beside the verdict and history of the SAME project, and a second
    # implementation of the stem would be free to drift from the first.
    return rl.reviewer_home() / "reviews" / (
        rl._state_stem(rl.canonical_project_key(proj)) + ".audits.jsonl")


def append_audit_record(proj, record):
    f = audit_log_file(proj)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    except OSError:
        pass
    return f


def read_audit_records(proj):
    """Every record for `proj`, oldest first. A corrupt line is skipped, not fatal: this is an
    append-only file written by short-lived processes, so a truncated tail must not destroy the
    history in front of it."""
    try:
        text = audit_log_file(proj).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def session_cursor_file(reviewer_session):
    sid = str(reviewer_session or "").strip()
    return None if not sid else (
        rl.reviewer_home() / "state" / (rl._sanitize(sid) + ".audit_cursor.json"))


_EMPTY_CURSOR = {"round": 0, "tokens_cumulative": None}


def session_cursor(reviewer_session):
    """`{round, tokens_cumulative}` for a REVIEWER session, across every project it reviewed.

    Deliberately NOT derived from a project's own audit log. A reviewer session belongs to a
    Claude session, not to a repository: one Claude session can gate commits in several repos,
    and codex's counter advances across all of them. Reading the round or the previous total
    from one project's log would skip the turns spent in the others and subtract their spend
    into this project's delta -- both fields silently wrong in an ordinary multi-repo session.

    A corrupt or unreadable cursor reads as empty, which costs one delta rather than producing
    a wrong one."""
    f = session_cursor_file(reviewer_session)
    if f is None:
        return dict(_EMPTY_CURSOR)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(_EMPTY_CURSOR)
    if not isinstance(data, dict):
        return dict(_EMPTY_CURSOR)
    rnd, tok = data.get("round"), data.get("tokens_cumulative")
    return {
        "round": rnd if isinstance(rnd, int) and rnd >= 0 else 0,
        "tokens_cumulative": tok if isinstance(tok, int) else None,
    }


def advance_session_cursor(reviewer_session, tokens_cumulative):
    """Consume the cursor for one attempt: returns `{round, previous}` and persists the new
    state. `previous` is the last cumulative total seen in this reviewer session, whichever
    project it was spent in.

    An attempt that reports no total (a timeout, a crash) still advances the round and KEEPS the
    last known total: the turn spent tokens even though it never printed a footer, so the next
    delta must span it rather than restart from nothing."""
    before = session_cursor(reviewer_session)
    after = {
        "round": before["round"] + 1,
        "tokens_cumulative": (tokens_cumulative if isinstance(tokens_cumulative, int)
                              else before["tokens_cumulative"]),
    }
    f = session_cursor_file(reviewer_session)
    if f is not None:
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(after), encoding="utf-8")
        except OSError:
            pass
    return {"round": after["round"], "previous": before["tokens_cumulative"]}


def _session_brief(proj, reviewer_session, backend):
    """(brief text, provenance) for this attempt.

    `current`    pi has no resumable session -- its brief rides along as an appended system
                 prompt on EVERY call, so the current working-tree brief IS what it saw.
    `session`    the brief codex was actually given when the session was created.
    `recomputed` no record exists (a session created before briefs were captured). The current
                 brief is the best available guess and is LABELLED as one: publishing it as
                 `session` would make a hash that is merely plausible look authoritative."""
    if backend == "pi":
        return rl.compose_brief(rl.canonical_project_key(proj)), "current"
    recorded = rl.session_brief(reviewer_session)
    if recorded is not None:
        return recorded, "session"
    return rl.compose_brief(rl.canonical_project_key(proj)), "recomputed"


def record_commit_attempt(proj, claude_session, reviewer_session, backend, prompt,
                          code, out, err, completed, approved, verdict_file,
                          change_block="", ignore_matcher=None):
    """Append one record for this commit-review attempt and return it (None if it could not be
    built or written).

    `change_block` is the staged-change annotation this round was sent, stored by content. It is
    the ONE prompt part that varies per round and carries the CHANGED/UNCHANGED labels, so
    without it the record cannot answer "did the reviewer see that the file had changed?" --
    which is exactly the question a stale restated finding raises. Defaults to "" so a caller
    that does not pass it still gets a record; the gate is fail-CLOSED and instrumentation may
    never be the reason it fires.

    Swallows every exception, including programming errors: the commit gate is fail-CLOSED, so a
    crash here would deny a commit the reviewer approved. Instrumentation is never allowed to be
    the reason a gate fires."""
    try:
        # `or` would be wrong here: a footer reporting 0 is falsy, and the fallback would then
        # silently prefer a stale number from the other stream.
        tokens = parse_codex_tokens(out)
        if tokens is None:
            tokens = parse_codex_tokens(err)
        cursor = advance_session_cursor(reviewer_session, tokens)
        previous = cursor["previous"]
        trace = parse_exec_trace(err)
        staged = rl.staged_blob_shas(proj)
        files, lines = staged_size(proj)
        # Counters for reviewerAgent-ignore.md exclusions -- 0 when no ignore file or no
        # staged path matches. Surfaced in the reviewer log line so an operator can see the
        # exclusion mechanism working.
        ignore_matched_files, ignore_matched_lines = rl.matched_counts(proj, ignore_matcher)
        invocation = rl.last_invocation() or {}
        # The codex streaming audit records per-turn usage on the invocation (there is no stdout
        # "tokens used" footer in --json mode). These are PER-TURN counts, kept alongside the
        # cumulative footer figure rather than folded into it, so advance_session_cursor's
        # cumulative-delta logic is untouched. Absent for pi/claude and the plain (non-streaming)
        # codex paths, which leave them None.
        usage = invocation.get("turn_usage") or {}
        capsule = rl.reviewer_capsule(proj, commit_audit=True, claude_session_id=claude_session)
        commit_prompt = rl.PI_COMMIT_PROMPT if backend == "pi" else rl.COMMIT_PROMPT
        brief, brief_source = _session_brief(proj, reviewer_session, backend)

        record = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "project": rl.canonical_project_key(proj),
            "claude_session": claude_session,
            "reviewer_session": reviewer_session,
            "round": cursor["round"],
            "backend": backend,
            "model": rl.reviewer_model(backend),
            "exit": code,
            "completed": bool(completed),
            "approved": bool(approved),
            "required_fixes": count_required_fixes(out, completed, approved),
            "verdict_file": verdict_file,
            "dur_s": invocation.get("dur_s"),
            # Streaming instrumentation (codex --json only; None elsewhere): time to the first
            # event, the largest between-event gap, and whether an idle kill forced one retry.
            "t_first_event_s": invocation.get("t_first_event_s"),
            "max_gap_s": invocation.get("max_gap_s"),
            "idle_retried": int(invocation.get("idle_retried") or 0),
            "tokens_cumulative": tokens,
            "tokens_delta": (tokens - previous)
                            if (tokens is not None and previous is not None) else None,
            "turn_input_tokens": usage.get("input_tokens"),
            "turn_output_tokens": usage.get("output_tokens"),
            "turn_cached_tokens": usage.get("cached_input_tokens"),
            "turn_reasoning_tokens": usage.get("reasoning_output_tokens"),
            "head": _head(proj),
            "staged": staged,
            "staged_files": files,
            "staged_lines": lines,
            "ignore_matched_files": ignore_matched_files,
            "ignore_matched_lines": ignore_matched_lines,
            "exec_trace": [e["cmd"] for e in trace],
            # Failures only. exec_trace already lists every command, so repeating the
            # successes here would just be a second copy of it; what is not recoverable
            # from that list is WHY a command did not count as evidence.
            "exec_failures": [{"cmd": e["cmd"], "code": e["code"]}
                              for e in trace if e.get("code") != 0],
            "request": {
                "timeout_s": rl.commit_review_timeout(),
                "argv": redact_argv(invocation.get("argv") or [], prompt),
            },
            "static": {
                "commit_prompt_sha256": store_prompt_part(commit_prompt),
                "capsule_sha256": store_prompt_part(capsule),
                "brief_sha256": store_prompt_part(brief),
                "brief_source": brief_source,
                # None, not a hash of "": a first audit sends NO annotation, and hashing the
                # empty string would make that indistinguishable from having sent an empty one.
                "change_block_sha256": (
                    store_prompt_part(change_block) if change_block else None),
            },
            "assembled_sha256": sha256_text(prompt),
        }
        record.update(exec_counters(trace))
        append_audit_record(proj, record)
        return record
    except Exception:  # noqa: BLE001 -- see docstring: never block the gate
        return None
