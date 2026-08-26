# SPDX-License-Identifier: MPL-2.0
"""Per-audit records, so a real commit review can be re-issued to a different model.

Today 2 of 1810 logged reviews record which model produced them, the verdict is truncated to 800
characters in the log, and nothing says what the reviewer actually DID -- so neither "is model B
better here" nor "did the reviewer take the delta instead of re-reading" can be answered.

Schema and rationale: REVIEW_COST_REDUCTION_PLAN.md, Phase A.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_audit as ra
import reviewer_backends as rb
import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))
    return tmp_path


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return str(path)


# --------------------------------------------------------------------------
# codex reports tokens CUMULATIVELY per session, so per-call cost is a delta. The number also
# appears in reviewer prose ("tokens used" is a phrase), which corrupted an earlier analysis --
# hence an anchored footer match rather than a search.
# --------------------------------------------------------------------------
def test_tokens_are_read_from_the_anchored_footer():
    assert ra.parse_codex_tokens("APPROVED\ntokens used\n25,385,151\n") == 25385151


def test_prose_mentioning_tokens_used_is_not_mistaken_for_the_footer():
    body = "REJECTED:\n1. the tokens used by the selector are wrong\n2. see loss.py\n"
    assert ra.parse_codex_tokens(body) is None


def test_prose_before_a_real_footer_does_not_win():
    body = "1. the tokens used here are wrong\ntokens used\n1,234\n"
    assert ra.parse_codex_tokens(body) == 1234


def test_absent_footer_is_none():
    assert ra.parse_codex_tokens("") is None
    assert ra.parse_codex_tokens("APPROVED\n") is None


def test_the_footer_is_read_through_codexs_ansi_styling():
    # Measured against the real log on 2026-07-30: codex writes an SGR reset between the phrase
    # and the number. A parser that does not expect it drops 248 of 1228 rows -- a 20% undercount
    # that looks like a clean parse, which is how it survived unnoticed.
    assert ra.parse_codex_tokens("APPROVED\ntokens used\x1b[0m\n20,644,364\n") == 20644364
    assert ra.parse_codex_tokens(
        "\x1b[1mtokens used\x1b[0m\x1b[2m\n\x1b[0m1,234\n") == 1234


# --------------------------------------------------------------------------
# codex echoes every shell command it runs to stderr. That is the only DIRECT evidence of
# whether the reviewer took the delta or re-read whole files; the harness was keeping 300
# characters of it and discarding the rest.
# --------------------------------------------------------------------------
_TRACE = (
    "exec\n/bin/bash -lc 'git diff --cached' in /repo\n succeeded in 12ms:\n"
    "some output here\n"
    "exec\n/bin/bash -lc 'git diff 814f4a422927b82f5f8a43f8fab6d3839e3983f2 :a.py' in /repo\n"
    " succeeded in 8ms:\n"
    "exec\n/bin/bash -lc 'git show :b.py' in /repo\n succeeded in 5ms:\n"
    "exec\n/bin/bash -lc 'python -m pytest tests -q' in /repo\n succeeded in 94000ms:\n"
    "exec\n/bin/bash -lc 'rg TODO src' in /repo\n failed in 3ms:\n"
    "codex\nAPPROVED\ntokens used\n25,385,151\n"
)


def test_the_exec_trace_yields_one_entry_per_command():
    trace = ra.parse_exec_trace(_TRACE)
    assert len(trace) == 5
    assert trace[0]["ms"] == 12
    assert trace[0]["ok"] is True
    assert trace[-1]["ok"] is False
    assert "git diff --cached" in trace[0]["cmd"]


def test_the_counters_separate_taking_the_delta_from_re_reading():
    c = ra.exec_counters(ra.parse_exec_trace(_TRACE))
    assert c["exec_delta_diffs"] == 1     # the `git diff <sha> :<path>` Phase C emits
    assert c["exec_full_reads"] == 2      # git show :b.py, rg
    assert c["exec_test_runs"] == 1
    assert c["exec_count"] == 5
    assert c["exec_ms_total"] == 12 + 8 + 5 + 94000 + 3


def test_the_trace_keeps_the_numeric_exit_code_not_just_a_boolean():
    """OBSERVED 2026-08-02. `test_runs=1` in the log was read as "the reviewer ran the suite",
    but the counter counts INVOCATIONS, and `ok` was never surfaced anywhere -- so an audit
    that tried pytest and got "command not found" was indistinguishable from one that ran it
    green. That ambiguity is what made a reviewer's "Pytest was unavailable in this
    environment" impossible to corroborate from the record.

    `ok` alone is not enough either: exit 1 (tests failed, so the suite RAN) and exit 127
    (not found, so it did not) are the same boolean and opposite facts."""
    trace = ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'pytest -q' in /r\n exited 127 in 4ms:\n"
        "exec\n/bin/bash -lc 'pytest -q' in /r\n exited 1 in 900ms:\n"
        "exec\n/bin/bash -lc 'pytest -q' in /r\n succeeded in 800ms:\n"
        "exec\n/bin/bash -lc 'rg TODO' in /r\n failed in 3ms:\n")
    assert [e["code"] for e in trace] == [127, 1, 0, None]
    # `ok` keeps its existing meaning so nothing reading it has to change.
    assert [e["ok"] for e in trace] == [False, False, True, False]


def test_the_counters_separate_a_test_run_that_failed_from_one_that_ran():
    """The whole point of the exit code: a count of attempts cannot answer "did it check?".
    `failed` is the honest denominator -- an unparseable outcome (codex's bare "failed in",
    with no number) counts as failed, because a command whose result we cannot read has not
    established anything either."""
    c = ra.exec_counters(ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'pytest -q' in /r\n exited 127 in 4ms:\n"
        "exec\n/bin/bash -lc 'pytest -q' in /r\n succeeded in 800ms:\n"
        "exec\n/bin/bash -lc 'git show :a.py' in /r\n exited 1 in 2ms:\n"))
    assert c["exec_test_runs"] == 2          # attempts, unchanged meaning
    assert c["exec_test_runs_failed"] == 1   # ...of which one never ran
    assert c["exec_failed"] == 2             # across all commands


def test_a_command_that_reads_and_runs_tests_counts_in_both():
    # REGRESSION. The categories are not a partition, and an `elif` between them meant any
    # command that did both was counted only as a test run -- undercounting exactly the number
    # these counters exist to track.
    c = ra.exec_counters(ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'cat setup.cfg && python -m pytest -q' in /r\n succeeded in 5ms:\n"))
    assert c["exec_full_reads"] == 1
    assert c["exec_test_runs"] == 1


def test_a_staged_diff_is_not_counted_as_a_delta_diff():
    # `git diff --cached` is the whole staged patch. Counting it as a delta would make the
    # Phase C measurement always look successful. The exclusion is deliberate and stays.
    c = ra.exec_counters(ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'git diff --cached' in /r\n succeeded in 1ms:\n"))
    assert c["exec_delta_diffs"] == 0


def test_the_counters_record_whether_the_staged_patch_WAS_read():
    """The question `exec_delta_diffs` and `exec_full_reads` cannot answer.

    Neither was built for it: one measures whether the reviewer took the Phase C delta, the
    other whether it re-read whole files. Asking them "did the reviewer see the change under
    review?" reads a zero as "read nothing" when the reviewer may have run `git diff --cached`
    -- the ONE command the prompt actually asks for -- and nothing else. That misreading is
    what put the unevidenced-approval rate at 14% when the measured figure is far lower.

    So the signal gets its own counter rather than a widened old one. Both spellings count;
    `--cached` and `--staged` are the same request."""
    for cmd in ("git diff --cached", "git diff --staged", "git --no-pager diff --cached -- a.py"):
        c = ra.exec_counters(ra.parse_exec_trace(
            "exec\n/bin/bash -lc '{}' in /r\n succeeded in 1ms:\n".format(cmd)))
        assert c["exec_staged_reads"] == 1, cmd
        assert c["exec_delta_diffs"] == 0, cmd


def test_a_failed_staged_read_is_not_evidence_the_change_was_seen():
    """Deliberately NOT the `exec_test_runs` convention of counting attempts with a separate
    _failed companion. This counter exists to answer whether the reviewer saw the change, and a
    command that exited nonzero showed it nothing -- so an attempt is not a sighting."""
    c = ra.exec_counters(ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'git diff --cached' in /r\n exited 128 in 2ms:\n"
        "exec\n/bin/bash -lc 'git diff --cached' in /r\n succeeded in 5ms:\n"))
    assert c["exec_staged_reads"] == 1
    assert c["exec_failed"] == 1


def test_an_unstaged_git_diff_is_not_a_staged_read():
    # A bare `git diff` is the UNSTAGED worktree delta -- the opposite of the review unit.
    c = ra.exec_counters(ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'git diff' in /r\n succeeded in 1ms:\n"
        "exec\n/bin/bash -lc 'git diff HEAD~1' in /r\n succeeded in 1ms:\n"))
    assert c["exec_staged_reads"] == 0


def test_text_that_only_mentions_a_staged_diff_is_not_a_staged_read():
    """Searches and diagnostic output cite commands without executing them.

    The audit metric is evidence that the staged bytes were actually shown to the reviewer, so
    matching command text inside another program's argument would turn absence of evidence into a
    false positive.
    """
    for cmd in (
            "rg 'git diff --cached' reviewer_audit.py",
            "echo git diff --cached",
            "python -c \"print('git diff --staged')\"",
    ):
        c = ra.exec_counters(ra.parse_exec_trace(
            "exec\n/bin/bash -lc {!r} in /r\n succeeded in 1ms:\n".format(cmd)))
        assert c["exec_staged_reads"] == 0, cmd


def test_a_nonzero_exit_is_its_own_entry_not_swallowed_into_the_next(tmp_path):
    # REGRESSION, found in real recorded stderr: codex reports a failing command as
    # "exited 1 in 0ms", not "failed in". Knowing only succeeded/failed made the non-greedy
    # match run on to the NEXT command's outcome line, merging two commands into one entry --
    # so a failed read vanished from the counters and its successor's text was mislabelled.
    trace = ra.parse_exec_trace(
        "exec\n/bin/bash -lc 'rg missing' in /r\n exited 1 in 3ms:\n"
        "exec\n/bin/bash -lc 'git show :a.py' in /r\n succeeded in 9ms:\n")
    assert len(trace) == 2
    assert trace[0]["ok"] is False and trace[0]["ms"] == 3
    assert trace[1]["ok"] is True
    assert "git show" in trace[1]["cmd"] and "rg missing" not in trace[1]["cmd"]
    assert ra.exec_counters(trace)["exec_count"] == 2


def test_an_empty_or_traceless_stderr_gives_zero_counters():
    for text in ("", "no commands here at all\n"):
        c = ra.exec_counters(ra.parse_exec_trace(text))
        assert c["exec_count"] == 0 and c["exec_ms_total"] == 0


def test_ansi_styling_does_not_hide_the_trace():
    # Same styling that hid a fifth of the token footers. Real codex stderr is a terminal
    # stream, so every parser here reads it AFTER the escapes are stripped.
    trace = ra.parse_exec_trace(
        "\x1b[38;5;8mexec\x1b[0m\n/bin/bash -lc 'git show :a.py' in /r\n"
        " \x1b[32msucceeded\x1b[0m in 4ms:\n")
    assert len(trace) == 1
    assert trace[0]["ms"] == 4 and trace[0]["ok"] is True
    assert ra.exec_counters(trace)["exec_full_reads"] == 1


# --------------------------------------------------------------------------
# Staged size: the dominant input, and the only thing token counts can be normalized against.
# --------------------------------------------------------------------------
def test_staged_size_counts_files_and_lines(tmp_path):
    proj = Path(_repo(tmp_path / "sz"))
    (proj / "a.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    (proj / "b.py").write_text("only\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "a.py", "b.py"], check=True)
    files, lines = ra.staged_size(str(proj))
    assert files == 2
    assert lines == 4  # 3 added + 1 added, no deletions


def test_staged_size_of_a_clean_tree_is_zero(tmp_path):
    assert ra.staged_size(_repo(tmp_path / "clean")) == (0, 0)


def test_staged_size_outside_a_repo_is_zero(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    assert ra.staged_size(str(d)) == (0, 0)


def test_a_binary_file_does_not_break_the_line_count(tmp_path):
    # numstat reports "-\t-\tpath" for binary content; treating "-" as an int raises.
    proj = Path(_repo(tmp_path / "bin"))
    (proj / "blob.bin").write_bytes(bytes(range(256)))
    (proj / "t.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(proj), "add", "blob.bin", "t.txt"], check=True)
    files, lines = ra.staged_size(str(proj))
    assert files == 2 and lines == 1


# --------------------------------------------------------------------------
# Prompt parts: a hash does NOT make a part recoverable. reviewerAgent.md is read from the
# WORKING TREE and need not be tracked, and the live install is not a git checkout.
# --------------------------------------------------------------------------
def test_a_prompt_part_is_stored_by_content_and_is_recoverable():
    sha = ra.store_prompt_part("BRIEF TEXT")
    assert len(sha) == 64
    assert ra.load_prompt_part(sha) == "BRIEF TEXT"


def test_storing_the_same_part_twice_writes_one_file():
    a = ra.store_prompt_part("SAME")
    b = ra.store_prompt_part("SAME")
    assert a == b
    assert len(list(ra.prompt_parts_dir().iterdir())) == 1


def test_different_parts_are_kept_apart():
    assert ra.store_prompt_part("ONE") != ra.store_prompt_part("TWO")
    assert ra.load_prompt_part(ra.store_prompt_part("ONE")) == "ONE"


def test_an_unknown_part_hash_is_none_not_an_error():
    assert ra.load_prompt_part("0" * 64) is None


# --------------------------------------------------------------------------
# The record itself
# --------------------------------------------------------------------------
def test_records_append_and_read_back_in_order(tmp_path):
    proj = _repo(tmp_path / "p1")
    ra.append_audit_record(proj, {"n": 1})
    ra.append_audit_record(proj, {"n": 2})
    assert [r["n"] for r in ra.read_audit_records(proj)] == [1, 2]


def test_records_are_per_project(tmp_path):
    one, two = _repo(tmp_path / "p2"), _repo(tmp_path / "p3")
    ra.append_audit_record(one, {"n": "one"})
    ra.append_audit_record(two, {"n": "two"})
    assert [r["n"] for r in ra.read_audit_records(one)] == ["one"]
    assert [r["n"] for r in ra.read_audit_records(two)] == ["two"]


def test_a_corrupt_line_does_not_destroy_the_rest(tmp_path):
    proj = _repo(tmp_path / "p4")
    ra.append_audit_record(proj, {"n": 1})
    with ra.audit_log_file(proj).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    ra.append_audit_record(proj, {"n": 2})
    assert [r["n"] for r in ra.read_audit_records(proj)] == [1, 2]


def test_the_token_delta_is_taken_within_one_reviewer_session():
    # Cumulative counters from DIFFERENT reviewer sessions are unrelated numbers; subtracting
    # across them would produce nonsense, sometimes negative.
    assert ra.session_cursor("sid-a") == {"round": 0, "tokens_cumulative": None}
    assert ra.advance_session_cursor("sid-a", 1000) == {"round": 1, "previous": None}
    assert ra.advance_session_cursor("sid-b", 9999) == {"round": 1, "previous": None}
    assert ra.advance_session_cursor("sid-a", 1400) == {"round": 2, "previous": 1000}


def test_an_attempt_with_no_token_total_still_advances_the_round(tmp_path):
    # A timeout reports no footer, but the turn still spent tokens. Dropping the last known
    # total would make the NEXT delta a nonsense number instead of one spanning both turns.
    ra.advance_session_cursor("s", 500)
    assert ra.advance_session_cursor("s", None) == {"round": 2, "previous": 500}
    assert ra.advance_session_cursor("s", 900) == {"round": 3, "previous": 500}


def test_the_cursor_survives_a_corrupt_file(tmp_path):
    ra.advance_session_cursor("s", 100)
    ra.session_cursor_file("s").write_text("{not json", encoding="utf-8")
    assert ra.session_cursor("s") == {"round": 0, "tokens_cumulative": None}


def test_a_reviewer_session_spanning_two_projects_keeps_one_token_series(tmp_path):
    # REGRESSION. A reviewer session belongs to a CLAUDE session, not to a repository: one
    # Claude session can gate commits in several repos, and codex's counter advances across all
    # of them. Reading the previous total out of THIS project's log skipped the turns spent in
    # the other repo and subtracted their spend into this project's delta.
    a, b = _repo(tmp_path / "pa"), _repo(tmp_path / "pb")

    def attempt(proj, total):
        return ra.record_commit_attempt(
            proj=proj, claude_session="cs", reviewer_session="rs", backend="codex",
            prompt="PROMPT", code=0, out="APPROVED\ntokens used\n{:,}\n".format(total),
            err="", completed=True, approved=True, verdict_file=None)

    attempt(a, 1000)
    attempt(b, 1600)
    attempt(a, 2000)

    recs = ra.read_audit_records(a)
    assert [r["round"] for r in recs] == [1, 3]      # the repo-B turn is not skipped
    assert recs[1]["tokens_delta"] == 400            # 2000 - 1600, not 2000 - 1000
    assert ra.read_audit_records(b)[0]["tokens_delta"] == 600


def test_the_recorded_brief_is_the_one_attached_to_the_session(tmp_path):
    # REGRESSION. codex is briefed ONCE, at session creation, and every later audit resumes the
    # id. Re-reading reviewerAgent.md at audit time meant an edit mid-session silently rewrote
    # what the record claimed the reviewer had been given.
    proj = Path(_repo(tmp_path / "brief"))
    (proj / "reviewerAgent.md").write_text("ORIGINAL PROJECT BRIEF\n", encoding="utf-8")
    rl.record_session_brief("rs-b", "THE BRIEF THAT WAS SENT")
    (proj / "reviewerAgent.md").write_text("EDITED AFTER THE SESSION STARTED\n", encoding="utf-8")

    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="rs-b", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None)
    assert ra.load_prompt_part(rec["static"]["brief_sha256"]) == "THE BRIEF THAT WAS SENT"
    assert rec["static"]["brief_source"] == "session"


def test_an_unrecorded_brief_is_labelled_rather_than_guessed(tmp_path):
    # Sessions created before this was captured have no record. Substituting the current brief
    # without saying so would publish a hash that looks authoritative and is not.
    proj = Path(_repo(tmp_path / "nobrief"))
    (proj / "reviewerAgent.md").write_text("CURRENT\n", encoding="utf-8")
    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="rs-unknown", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None)
    assert rec["static"]["brief_source"] == "recomputed"


def test_pi_records_the_current_brief_because_pi_is_re_briefed_every_call(tmp_path):
    # pi has no resumable session: the brief rides along as an appended system prompt on EVERY
    # call, so the current working-tree brief IS what it saw.
    proj = Path(_repo(tmp_path / "pibrief"))
    (proj / "reviewerAgent.md").write_text("PI SEES THIS\n", encoding="utf-8")
    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="pi:new", backend="pi",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None)
    assert rec["static"]["brief_source"] == "current"
    assert "PI SEES THIS" in ra.load_prompt_part(rec["static"]["brief_sha256"])


# --------------------------------------------------------------------------
# argv: recorded from the ONE place both backends actually execute, so a knob added later is
# captured without anyone remembering to extend the schema.
# --------------------------------------------------------------------------
def test_the_executed_argv_and_duration_are_observable_after_a_run():
    rl.reset_last_invocation()
    rl._run([sys.executable, "-c", "print('hi')"], timeout=30)
    inv = rl.last_invocation()
    assert inv["argv"][0] == sys.executable
    assert inv["dur_s"] >= 0


def test_a_missing_binary_still_records_the_attempt():
    rl.reset_last_invocation()
    code, _, _ = rl._run(["/nonexistent/reviewer-binary-xyz"], timeout=5)
    assert code == 127
    assert rl.last_invocation()["argv"] == ["/nonexistent/reviewer-binary-xyz"]


def test_a_timeout_still_records_the_attempt():
    rl.reset_last_invocation()
    code, _, _ = rl._run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=1)
    assert code == rl.TIMEOUT_EXIT
    inv = rl.last_invocation()
    assert inv["argv"][0] == sys.executable
    assert inv["dur_s"] >= 1


def test_the_prompt_is_redacted_out_of_a_recorded_argv():
    # The prompt is large and carries operator prose; blocks + assembled_sha256 already cover it.
    argv = ["codex", "exec", "-C", "/repo", "resume", "sid", "THE WHOLE PROMPT TEXT"]
    red = ra.redact_argv(argv, "THE WHOLE PROMPT TEXT")
    assert "THE WHOLE PROMPT TEXT" not in red
    assert red[:-1] == argv[:-1]
    assert red[-1].startswith("sha256:")


# --------------------------------------------------------------------------
# required_fixes: how much work a rejection actually demanded
#
# The metric that could NOT be recovered after the fact. `verdict_file` is a
# per-session path that later reviews OVERWRITE -- 166 recorded rejections
# mapped to only 6 distinct paths -- so the count has to be taken at record
# time, where the verdict text is still in hand.
# --------------------------------------------------------------------------
def test_a_rejection_counts_its_numbered_required_fixes():
    out = ("REJECTED:\n"
           "1. First problem, in `a.py:10`.\n"
           "2. Second problem, in `b.py:20`.\n"
           "3. Third problem.\n")
    assert ra.count_required_fixes(out, completed=True, approved=False) == 3


def test_suggestions_are_not_counted_as_required_fixes():
    # The whole point of the SUGGESTIONS block is that it is NON-blocking, so
    # counting it would overstate what the rejection actually demanded.
    out = ("REJECTED:\n"
           "1. A blocking problem.\n"
           "2. Another blocking problem.\n"
           "SUGGESTIONS:\n"
           "1. A non-blocking nicety.\n"
           "2. Another one.\n"
           "3. And a third.\n")
    assert ra.count_required_fixes(out, completed=True, approved=False) == 2


def test_a_wrapped_finding_is_one_fix_not_several():
    # Findings routinely wrap over several lines and cite dotted versions or
    # dates; only a line that OPENS with "<n>." starts a new item.
    out = ("REJECTED:\n"
           "1. The parser breaks on input like\n"
           "   2026-08-01 and on 3.11 style versions.\n"
           "2. A second real finding.\n")
    assert ra.count_required_fixes(out, completed=True, approved=False) == 2


def test_an_approved_verdict_has_zero_required_fixes():
    # Zero, not None: an approval genuinely demanded no fixes, and recording it
    # as a number is what makes "fixes per attempt" averageable across outcomes.
    assert ra.count_required_fixes("APPROVED", completed=True, approved=True) == 0


def test_an_incomplete_attempt_records_no_count_at_all():
    # A timeout, a crash or an empty response produced no verdict, so the
    # question does not apply. None, never 0 -- 0 would read as "demanded
    # nothing" and quietly drag the average down.
    assert ra.count_required_fixes("", completed=False, approved=False) is None
    assert ra.count_required_fixes(None, completed=False, approved=False) is None


def test_a_rejection_with_no_parseable_list_counts_zero_not_none():
    # Off-contract output still completed and still blocked the commit; it is a
    # real rejection with no enumerated fixes, which is itself worth seeing.
    assert ra.count_required_fixes("REJECTED: something went wrong",
                                   completed=True, approved=False) == 0


def test_the_staged_change_block_is_recorded_by_content(tmp_path):
    """Without this the record cannot answer "did the reviewer see the CHANGED row?".

    That question is not hypothetical. On 2026-08-01 a reviewer restated a stale rejection three
    times against a file that had demonstrably changed, and settling it meant replaying
    staged_change_block against blob shas recovered by hand, because commit_prompt, capsule and
    brief were content-addressed and the change block -- the one part that varies per round and
    carries the CHANGED labels -- was not (DELTA_INLINE_PLAN.md, Fix 2).
    """
    proj = Path(_repo(tmp_path / "cblock"))
    block = "STAGED SET vs YOUR LAST AUDIT (re-review #7):\n  CHANGED    doc.md\n"

    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="rs-c", backend="codex",
        prompt=block + "P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None, change_block=block)

    assert ra.load_prompt_part(rec["static"]["change_block_sha256"]) == block


def test_a_round_with_no_change_block_records_none(tmp_path):
    """A first audit has no prior state, so staged_change_block returns "". Storing a hash of the
    empty string would make "no annotation was sent" indistinguishable from "an empty one was"."""
    proj = Path(_repo(tmp_path / "noblock"))
    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="rs-d", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None, change_block="")
    assert rec["static"]["change_block_sha256"] is None


def test_recording_the_change_block_is_optional_for_callers(tmp_path):
    """record_commit_attempt swallows everything because the gate is fail-CLOSED. A caller that
    does not pass the new argument must still get a record, not a TypeError-shaped None."""
    proj = Path(_repo(tmp_path / "optblock"))
    rec = ra.record_commit_attempt(
        proj=str(proj), claude_session="cs", reviewer_session="rs-e", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None)
    assert rec is not None and rec["static"]["change_block_sha256"] is None


# --------------------------------------------------------------------------
# Strict codex verdict schema (docs/plans/codex_jsonl_streaming.md, Task 1).
#
# The codex commit audit now emits a schema-constrained JSON object
# {verdict, findings, suggestions} to its `-o` file. parse_codex_verdict_json
# accepts ONLY that exact shape AND the verdict/findings consistency the prompt
# contract requires (APPROVED => no findings, REJECTED => >=1 finding); anything
# else -> None so the gate fails CLOSED. codex_verdict_to_text reconstructs the
# canonical APPROVED/REJECTED text so is_approved / reviewer_audit stay unchanged.
# --------------------------------------------------------------------------
def test_verdict_json_valid_approved_no_findings():
    obj = rb.parse_codex_verdict_json(
        '{"verdict":"APPROVED","findings":[],"suggestions":[]}')
    assert obj == {"verdict": "APPROVED", "findings": [], "suggestions": []}


def test_verdict_json_valid_approved_with_suggestions():
    obj = rb.parse_codex_verdict_json(
        '{"verdict":"APPROVED","findings":[],"suggestions":["prefer x","cache y"]}')
    assert obj["verdict"] == "APPROVED"
    assert obj["findings"] == []
    assert obj["suggestions"] == ["prefer x", "cache y"]


def test_verdict_json_valid_rejected_with_findings_and_suggestions():
    obj = rb.parse_codex_verdict_json(
        '{"verdict":"REJECTED","findings":["off-by-one at a.py:10"],'
        '"suggestions":["rename z"]}')
    assert obj["verdict"] == "REJECTED"
    assert obj["findings"] == ["off-by-one at a.py:10"]
    assert obj["suggestions"] == ["rename z"]


@pytest.mark.parametrize("text", [
    "not json at all",
    "",
    "[]",                                                       # non-object top level (list)
    '"APPROVED"',                                               # non-object top level (string)
    "42",                                                       # non-object top level (number)
    "null",                                                     # non-object top level (null)
    '{"verdict":"APPROVED","findings":[]}',                     # missing suggestions
    '{"verdict":"APPROVED","suggestions":[]}',                  # missing findings
    '{"findings":[],"suggestions":[]}',                         # missing verdict
    '{"verdict":"APPROVED","findings":[],"suggestions":[],"extra":1}',   # extra key
    '{"verdict":"MAYBE","findings":[],"suggestions":[]}',       # verdict outside enum
    '{"verdict":"approved","findings":[],"suggestions":[]}',    # wrong case (enum is exact)
    '{"verdict":"APPROVED","findings":"none","suggestions":[]}',  # findings not a list
    '{"verdict":"APPROVED","findings":[],"suggestions":"x"}',     # suggestions not a list
    '{"verdict":"REJECTED","findings":[1,2],"suggestions":[]}',   # non-string finding element
    '{"verdict":"APPROVED","findings":[],"suggestions":[{"a":1}]}',  # non-string suggestion
    '{"verdict":"APPROVED","findings":["oops"],"suggestions":[]}',   # APPROVED w/ findings
    '{"verdict":"REJECTED","findings":[],"suggestions":["x"]}',      # REJECTED w/o findings
])
def test_verdict_json_strict_rejections_return_none(text):
    assert rb.parse_codex_verdict_json(text) is None


def test_verdict_json_never_raises_on_garbage():
    for bad in [None, "{", "{}", '{"verdict":null}', "\x00", "{}\n{}", "  "]:
        assert rb.parse_codex_verdict_json(bad) is None


def test_verdict_text_approved_no_suggestions():
    obj = {"verdict": "APPROVED", "findings": [], "suggestions": []}
    assert rb.codex_verdict_to_text(obj) == "APPROVED"


def test_verdict_text_approved_with_suggestions():
    obj = {"verdict": "APPROVED", "findings": [], "suggestions": ["do x", "do y"]}
    assert rb.codex_verdict_to_text(obj) == "APPROVED\nSUGGESTIONS:\n1. do x\n2. do y"


def test_verdict_text_rejected_with_findings():
    obj = {"verdict": "REJECTED", "findings": ["bug a", "bug b"], "suggestions": []}
    assert rb.codex_verdict_to_text(obj) == "REJECTED:\n1. bug a\n2. bug b"


def test_verdict_text_rejected_with_findings_and_suggestions():
    obj = {"verdict": "REJECTED", "findings": ["bug a"], "suggestions": ["tidy z"]}
    assert (rb.codex_verdict_to_text(obj)
            == "REJECTED:\n1. bug a\nSUGGESTIONS:\n1. tidy z")


def test_verdict_text_none_is_empty_string():
    assert rb.codex_verdict_to_text(None) == ""


def test_verdict_text_invalid_obj_is_empty_string():
    # not the output of a successful parse -> fail closed to ""
    assert rb.codex_verdict_to_text({"verdict": "APPROVED"}) == ""
    assert rb.codex_verdict_to_text("APPROVED") == ""
    assert rb.codex_verdict_to_text({"verdict": "MAYBE", "findings": [],
                                     "suggestions": []}) == ""


def test_verdict_text_fails_closed_on_inconsistent_or_extra_key_objects():
    """Regression (gate finding 2): codex_verdict_to_text must NOT reconstruct a contradictory or
    malformed object. An APPROVED-with-findings dict rendered as a bare "APPROVED" would silently
    drop the findings and approve what the parser refuses -- so to_text re-validates through the
    same strict check as parsing and fails closed to ""."""
    # APPROVED carrying findings -> reconstructing it would silently lose them; must be ""
    assert rb.codex_verdict_to_text(
        {"verdict": "APPROVED", "findings": ["dropped bug"], "suggestions": []}) == ""
    # REJECTED with no findings -> inconsistent; must be ""
    assert rb.codex_verdict_to_text(
        {"verdict": "REJECTED", "findings": [], "suggestions": ["x"]}) == ""
    # extra key -> not exactly the three keys; must be ""
    assert rb.codex_verdict_to_text(
        {"verdict": "APPROVED", "findings": [], "suggestions": [], "extra": 1}) == ""
    # missing key -> must be ""
    assert rb.codex_verdict_to_text({"verdict": "REJECTED", "findings": ["b"]}) == ""
    # non-string finding element -> must be ""
    assert rb.codex_verdict_to_text(
        {"verdict": "REJECTED", "findings": [1], "suggestions": []}) == ""


def test_verdict_text_reconstruction_round_trips_through_is_approved():
    ap = rb.codex_verdict_to_text(
        {"verdict": "APPROVED", "findings": [], "suggestions": ["s"]})
    rj = rb.codex_verdict_to_text(
        {"verdict": "REJECTED", "findings": ["f"], "suggestions": []})
    assert rl.is_approved(ap) is True
    assert rl.is_approved(rj) is False


def test_turn_usage_reads_last_turn_completed():
    stream = "\n".join([
        json.dumps({"type": "thread.started", "thread_id": "t1"}),
        json.dumps({"type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5}}),
        json.dumps({"type": "turn.completed",
                    "usage": {"input_tokens": 100, "output_tokens": 50,
                              "cached_input_tokens": 8, "reasoning_output_tokens": 20}}),
    ])
    assert rb.parse_codex_turn_usage(stream) == {
        "input_tokens": 100, "output_tokens": 50,
        "cached_input_tokens": 8, "reasoning_output_tokens": 20}


def test_turn_usage_absent_is_none():
    assert rb.parse_codex_turn_usage("") is None
    assert rb.parse_codex_turn_usage(json.dumps({"type": "thread.started"})) is None
    assert rb.parse_codex_turn_usage("not json\n{bad}") is None


def test_turn_usage_ignores_turn_completed_without_usage_dict():
    stream = "\n".join([
        json.dumps({"type": "turn.completed"}),                 # no usage
        json.dumps({"type": "turn.completed", "usage": "nope"}),  # usage not a dict
    ])
    assert rb.parse_codex_turn_usage(stream) is None
