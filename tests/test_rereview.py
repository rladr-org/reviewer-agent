# SPDX-License-Identifier: MPL-2.0
"""Tell a resumed reviewer WHICH staged files moved since its last audit.

The reviewer is one long-lived Codex session -- `sid=019f94b2...` served 8 audits over 2.5
hours on 2026-07-25 -- so every prior rejection sits in its context, complete with the line
numbers of the draft that has since changed. Nothing told it the staged set had moved.

The agent working in that repo reported the symptom as "the reviewer re-emits already-fixed
findings against stale doc line-positions", and neither of us could settle it: the reviewer
reads the staged diff live (there is no cached snapshot to go stale), but "the agent edited
after `git add` and never re-staged" produces an identical symptom. Per-file content hashes
decide it, and git already computes them -- the index blob sha changes iff the staged
content changed.

The annotation informs; it never gates. Skipping unchanged files would be wrong twice over:
a finding on an unchanged file is still valid and must still block, and findings are not
file-local (half the citations in that session pointed at a file the agent was not editing).
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

SID = "codex-sid-1"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_CONFIG_FILE", str(tmp_path / "missing.json"))


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(r), "config", "user.name", "t"], check=True)
    (r / "reviewerAgent.md").write_text("strict reviewer\n")
    (r / "base.py").write_text("base = 0\n")
    subprocess.run(["git", "-C", str(r), "add", "base.py"], check=True)
    subprocess.run(["git", "-C", str(r), "commit", "-q", "-m", "init"], check=True)
    return str(r)


def _write(repo, name, text):
    Path(repo, name).write_text(text, encoding="utf-8")


def _stage(repo, *names):
    subprocess.run(["git", "-C", repo, "add"] + list(names), check=True)


def _audit(repo, sid=SID, session="claude-1"):
    """One completed audit: what the reviewer would be told, then the record it leaves."""
    block = rl.staged_change_block(repo, session, sid)
    rl.record_audited_stage(repo, session, sid)
    return block


# --------------------------------------------------------------------------
# per-file content identity
# --------------------------------------------------------------------------
def test_staged_blob_shas_track_content_not_filenames(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    first = rl.staged_blob_shas(repo)
    assert set(first) == {"a.py"}

    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    assert rl.staged_blob_shas(repo)["a.py"] != first["a.py"]


def test_restaging_identical_content_keeps_the_same_sha(repo):
    # `git add` on an unmodified file is the no-op the agent's report hinged on.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    first = rl.staged_blob_shas(repo)
    _stage(repo, "a.py")
    assert rl.staged_blob_shas(repo) == first


def test_editing_without_restaging_does_not_change_the_staged_sha(repo):
    # THE failure mode we could not rule out: the working tree moves, the index does not.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    first = rl.staged_blob_shas(repo)
    _write(repo, "a.py", "x = 999\n")  # edited, NOT re-staged
    assert rl.staged_blob_shas(repo) == first


def test_a_deleted_staged_file_is_recorded(repo):
    subprocess.run(["git", "-C", repo, "rm", "-q", "base.py"], check=True)
    assert "base.py" in rl.staged_blob_shas(repo)


# --------------------------------------------------------------------------
# the annotation
# --------------------------------------------------------------------------
def test_the_first_audit_gets_no_annotation(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    assert rl.staged_change_block(repo, "claude-1", SID) == ""


def test_a_changed_file_is_reported_as_changed(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "CHANGED" in block and "a.py" in block


def test_an_unchanged_file_is_reported_as_unchanged(repo):
    _write(repo, "a.py", "x = 1\n")
    _write(repo, "b.py", "y = 1\n")
    _stage(repo, "a.py", "b.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    block = rl.staged_change_block(repo, "claude-1", SID)
    changed = [ln for ln in block.splitlines() if "CHANGED" in ln and "UNCHANGED" not in ln]
    unchanged = [ln for ln in block.splitlines() if "UNCHANGED" in ln]
    assert any("a.py" in ln for ln in changed)
    assert any("b.py" in ln for ln in unchanged)


def test_a_newly_staged_file_is_reported_as_added(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "tests.py", "def test_x(): pass\n")
    _stage(repo, "tests.py")
    assert "ADDED" in rl.staged_change_block(repo, "claude-1", SID)


def test_a_file_dropped_from_the_stage_is_reported_as_removed(repo):
    _write(repo, "a.py", "x = 1\n")
    _write(repo, "scratch.py", "junk = 1\n")
    _stage(repo, "a.py", "scratch.py")
    _audit(repo)
    subprocess.run(["git", "-C", repo, "restore", "--staged", "scratch.py"], check=True)
    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "REMOVED" in block and "scratch.py" in block


def test_an_identical_stage_says_the_fix_was_never_staged(repo):
    # The jackpot case: settles "the reviewer cannot see my fix" on the spot.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")  # edited but NOT re-staged
    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "did not stage" in block.lower()
    assert "CHANGED" not in block


# --------------------------------------------------------------------------
# the instructions carried with it
# --------------------------------------------------------------------------
def test_changed_files_are_marked_as_needing_rederivation(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    low = rl.staged_change_block(repo, "claude-1", SID).lower()
    assert "stale" in low and "re-derive" in low


def test_findings_on_unchanged_files_are_kept_alive(repo):
    # Unchanged must never mean unreviewed: a finding raised last round still blocks.
    _write(repo, "a.py", "x = 1\n")
    _write(repo, "b.py", "y = 1\n")
    _stage(repo, "a.py", "b.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    low = rl.staged_change_block(repo, "claude-1", SID).lower()
    assert "still stand" in low or "restate" in low
    assert "do not invent" in low


def test_the_reviewer_is_told_to_judge_the_set_as_a_whole(repo):
    # Findings are not file-local -- a change in one staged file can overturn a conclusion
    # about another, so "UNCHANGED" must not read as "settled".
    _write(repo, "a.py", "x = 1\n")
    _write(repo, "b.py", "y = 1\n")
    _stage(repo, "a.py", "b.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    assert "as a whole" in rl.staged_change_block(repo, "claude-1", SID).lower()


def test_the_round_number_increments(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    assert "#2" in _audit(repo)
    _write(repo, "a.py", "x = 3\n")
    _stage(repo, "a.py")
    assert "#3" in rl.staged_change_block(repo, "claude-1", SID)


# --------------------------------------------------------------------------
# when the annotation must NOT be sent
# --------------------------------------------------------------------------
def test_a_fresh_reviewer_session_gets_no_annotation(repo):
    # A re-briefed reviewer holds no prior findings. Telling it "restate them for unchanged
    # files" would leave those files unreviewed, or invite invention.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo, sid=SID)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    assert rl.staged_change_block(repo, "claude-1", "a-different-codex-session") == ""


def test_a_new_claude_session_gets_no_annotation(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo, session="claude-1")
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    assert rl.staged_change_block(repo, "claude-2", SID) == ""


def test_a_landed_commit_clears_the_annotation(repo):
    # HEAD moving means the previous audit's commit went through; whatever is staged now is
    # fresh work and deserves a full review.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "landed"], check=True)
    _write(repo, "c.py", "z = 1\n")
    _stage(repo, "c.py")
    assert rl.staged_change_block(repo, "claude-1", SID) == ""


def test_a_corrupt_record_falls_back_to_a_full_review(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    f = rl.audited_stage_file(repo, "claude-1")
    f.write_text("{not json", encoding="utf-8")
    assert rl.staged_change_block(repo, "claude-1", SID) == ""


# --------------------------------------------------------------------------
# The delta itself. Knowing WHICH files moved still left "re-read the whole file" as the only
# instruction, which is the duplicated effort the annotation exists to remove. The bytes the
# reviewer already audited are addressable: `git add` wrote them, and record_audited_stage kept
# the sha. `git diff <blob> :<path>` reads the INDEX and writes nothing to the repository.
# --------------------------------------------------------------------------
_DELTA_CMD = re.compile(r"git diff ([0-9a-f]{40}) (\S+)")


def _delta_commands(block):
    """The DISTINCT delta commands the block names, in first-seen order.

    Deduped because the block names each command TWICE on purpose -- once in the path's row as
    inventory, once in the instruction paragraph as the thing to run, so the reviewer never has
    to match a label back to a row. The tests below are about whether the command is correct,
    runnable and index-reading; how many times it is printed is pinned separately by
    `test_the_fallback_instruction_carries_the_literal_command_not_a_reference`.
    """
    seen, out = set(), []
    for hit in _DELTA_CMD.findall(block):
        if hit not in seen:
            seen.add(hit)
            out.append(hit)
    return out


@pytest.fixture
def command_mode(monkeypatch):
    """Pin the block to the COMMAND path.

    Since DELTA_INLINE_PLAN.md the default is to SHOW a small diff rather than name a command to
    fetch it, so these tests would otherwise find no command to check. The command did not go
    away -- it is now the fallback for a diff too large to inline, and for inlining turned off --
    and everything asserted below is exactly what must remain true on that fallback: the command
    has to run, read the INDEX, and leave the repository untouched. The default inline path is
    covered by the tests at the foot of this file.
    """
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")


def test_a_changed_path_carries_a_runnable_delta_command(repo, command_mode):
    _write(repo, "a.py", "one\ntwo\nthree\n")
    _stage(repo, "a.py")
    old = rl.staged_blob_shas(repo)["a.py"]
    _audit(repo)
    _write(repo, "a.py", "one\ntwo\nTHREE\n")
    _stage(repo, "a.py")

    found = _delta_commands(rl.staged_change_block(repo, "claude-1", SID))
    assert found == [(old, ":a.py")], found


def test_the_named_delta_command_actually_runs_and_reads_the_index(repo, command_mode):
    # A prompt that names a command which does not work is worse than naming none: the reviewer
    # burns a call, gets an error, and falls back having lost the round. So run it for real --
    # and prove it reads the INDEX, since a working-tree diff would show edits that are not in
    # the commit under review.
    _write(repo, "a.py", "one\ntwo\nthree\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "one\ntwo\nTHREE\n")
    _stage(repo, "a.py")
    _write(repo, "a.py", "one\ntwo\nTHREE\nNOT_STAGED\n")  # edited after `git add`

    (sha, spec), = _delta_commands(rl.staged_change_block(repo, "claude-1", SID))
    p = subprocess.run(["git", "-C", repo, "diff", sha, spec],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    assert "+THREE" in p.stdout and "-three" in p.stdout
    assert "NOT_STAGED" not in p.stdout


def test_the_delta_command_leaves_the_repository_untouched(repo, command_mode):
    _write(repo, "a.py", "one\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "two\n")
    _stage(repo, "a.py")
    (sha, spec), = _delta_commands(rl.staged_change_block(repo, "claude-1", SID))
    before = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                            capture_output=True, text=True).stdout
    subprocess.run(["git", "-C", repo, "diff", sha, spec], capture_output=True, text=True)
    after = subprocess.run(["git", "-C", repo, "status", "--porcelain"],
                           capture_output=True, text=True).stdout
    assert before == after


def test_the_reviewer_is_told_to_diff_first_and_re_read_only_if_needed(repo):
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    low = rl.staged_change_block(repo, "claude-1", SID).lower()
    assert "stale" in low                      # the old warning survives
    assert "since your last audit" in low
    assert "git show :" in low                 # full re-read is still available as the fallback


def test_an_added_path_gets_no_delta_command(repo):
    # Nothing was audited to diff against.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "new.py", "y = 1\n")
    _stage(repo, "new.py")
    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "ADDED" in block
    assert _delta_commands(block) == []


def test_a_missing_blob_falls_back_to_a_full_re_read(repo):
    # Loose objects survive gc for two weeks by default, but "by default" is not a guarantee,
    # and naming an unresolvable object would send the reviewer into an error.
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    f = rl.audited_stage_file(repo, "claude-1")
    rec = json.loads(f.read_text(encoding="utf-8"))
    rec["files"]["a.py"] = "0" * 40  # an object that is not in the database
    f.write_text(json.dumps(rec), encoding="utf-8")
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")

    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "CHANGED" in block and "a.py" in block
    assert _delta_commands(block) == []
    assert "re-derive" in block.lower()


def test_pi_is_never_handed_a_shell_command(repo, monkeypatch):
    # pi is hermetically read-only: no shell, and its git_read interface here is diff/status
    # only, so it has no way to run this. Backlog: extend that extension.
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    _write(repo, "a.py", "x = 1\n")
    _stage(repo, "a.py")
    _audit(repo)
    _write(repo, "a.py", "x = 2\n")
    _stage(repo, "a.py")
    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "CHANGED" in block
    assert _delta_commands(block) == []
    assert "git diff" not in block
    assert "re-derive" in block.lower()


# --------------------------------------------------------------------------
# the hook uses it
# --------------------------------------------------------------------------
def _run_commit(repo, session="s1"):
    env = dict(os.environ)
    env.update({"REVIEWER_HOME": os.environ["REVIEWER_HOME"],
                "REVIEWER_CONFIG_FILE": os.environ["REVIEWER_CONFIG_FILE"],
                "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_VERDICT": "REJECTED",
                "FAKE_CODEX_FINDINGS": "no",
                "FAKE_CODEX_ARGV_FILE": str(Path(repo) / "argv.jsonl")})
    subprocess.run(
        [sys.executable, str(ROOT / "commit_hook.py")],
        input=json.dumps({"cwd": repo, "session_id": session, "tool_name": "Bash",
                          "tool_input": {"command": "git commit -m x"}}),
        capture_output=True, text=True, env=env,
    )
    dump = Path(repo) / "argv.jsonl"
    args = []
    if dump.exists():
        for line in dump.read_text(encoding="utf-8").splitlines():
            args.extend(json.loads(line))
    return args


def test_the_second_commit_attempt_tells_the_reviewer_what_moved(repo):
    rl.set_active(repo)
    _write(repo, "doc.md", "draft one\n")
    _stage(repo, "doc.md")
    _run_commit(repo)
    Path(repo, "argv.jsonl").unlink()

    _write(repo, "doc.md", "draft two, much longer\n")
    _stage(repo, "doc.md")
    args = _run_commit(repo)
    assert any("CHANGED" in a and "doc.md" in a for a in args), args


def test_the_hook_reports_an_unstaged_fix_on_the_retry(repo):
    rl.set_active(repo)
    _write(repo, "doc.md", "draft one\n")
    _stage(repo, "doc.md")
    _run_commit(repo)
    Path(repo, "argv.jsonl").unlink()

    _write(repo, "doc.md", "fixed but never staged\n")  # no `git add`
    args = _run_commit(repo)
    assert any("did not stage" in a.lower() for a in args), args


# --------------------------------------------------------------------------
# The delta is SHOWN, not requested (DELTA_INLINE_PLAN.md)
#
# Measured defect: over this repo's audit history, 3 of 57 rounds whose staged content genuinely
# moved did ZERO reads and ZERO exec calls and restated a stale REJECTION. All three were a single
# doc answered in 8.8-17.6s, against 15-385s for the 54 rounds that did read. The reviewer is a
# RESUMED session, so its previous verdict is in its context: skipping the delta means answering
# from memory, and memory holds the finding it raised last time.
#
# Naming a command the reviewer MAY run makes correctness depend on it electing to spend a tool
# call. Putting the diff in the prompt removes that choice.
# --------------------------------------------------------------------------
def test_a_changed_paths_diff_is_in_the_prompt_not_merely_referenced(repo):
    """THE regression test for the defect. The changed TEXT must be present, not a way to get it."""
    _write(repo, "doc.md", "alpha line\n")
    _stage(repo, "doc.md")
    _audit(repo)

    _write(repo, "doc.md", "beta line\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "beta line" in block, "the new content is not in the prompt:\n" + block
    assert "alpha line" in block, "the replaced content is not in the prompt:\n" + block
    assert "@@" in block, "no hunk header, so no real diff was inlined:\n" + block


def test_the_inlined_diff_reads_the_index_not_the_working_tree(repo):
    """An edit made after `git add` must NOT appear: the reviewer may never cite a line that is
    absent from the commit it is approving."""
    _write(repo, "doc.md", "staged one\n")
    _stage(repo, "doc.md")
    _audit(repo)

    _write(repo, "doc.md", "staged two\n")
    _stage(repo, "doc.md")
    _write(repo, "doc.md", "UNSTAGED EDIT\n")  # after the add, deliberately
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "staged two" in block
    assert "UNSTAGED EDIT" not in block, "working-tree content leaked into the prompt:\n" + block


def test_a_diff_over_the_per_path_cap_is_not_inlined_and_says_so(repo, monkeypatch):
    """A partially-inlined block must never read as complete."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "40")
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)

    _write(repo, "doc.md", "".join("padding line {}\n".format(i) for i in range(200)))
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "padding line 199" not in block, "an over-cap diff was inlined anyway"
    assert "git diff" in block, "the command must survive so the reviewer can still fetch it"
    assert "too large" in block.lower(), "silent truncation reads as complete:\n" + block


def test_the_whole_block_cap_holds_when_each_path_fits_individually(repo, monkeypatch):
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "100000")
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_TOTAL_MAX", "300")
    for n in ("a.md", "b.md", "c.md"):
        _write(repo, n, "one\n")
    _stage(repo, "a.md", "b.md", "c.md")
    _audit(repo)

    for n in ("a.md", "b.md", "c.md"):
        _write(repo, n, "".join("row {} of {}\n".format(i, n) for i in range(20)))
    _stage(repo, "a.md", "b.md", "c.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "too large" in block.lower() or "budget" in block.lower(), block
    assert "git diff" in block, "paths that missed the budget keep their command"


def test_a_zero_cap_disables_inlining_without_attempting_a_diff(repo, monkeypatch):
    """`0` is a DISABLED sentinel, not a budget of zero. Treated as a budget every nonempty diff
    would 'exceed' it and gain a too-large note, so the rollback would emit a block that has never
    existed. It must short-circuit BEFORE the diff runs, which is why the runner raises here."""
    import reviewer_prompts

    def must_not_run(*a, **k):
        raise AssertionError("a disabled cap still attempted a diff")

    # Patched on reviewer_prompts, not rl: staged_change_block lives there and resolves this name
    # in that module's globals (LIB_SPLIT_PLAN.md 5.3).
    monkeypatch.setattr(reviewer_prompts, "_delta_text", must_not_run)
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")

    _write(repo, "doc.md", "before\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "after\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "git diff" in block, "today's command row must survive"
    assert "@@" not in block and "too large" not in block.lower(), (
        "a disabled cap must emit today's block exactly:\n" + block)


def test_a_diff_that_fails_at_runtime_keeps_the_command_and_does_not_raise(repo, monkeypatch):
    """This code sits in front of a FAIL-CLOSED gate: it may degrade, never raise."""
    import reviewer_prompts

    def boom(*a, **k):
        raise OSError("git exploded")

    monkeypatch.setattr(reviewer_prompts, "_delta_text", boom)
    _write(repo, "doc.md", "before\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "after\n")
    _stage(repo, "doc.md")

    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "git diff" in block and "CHANGED" in block, block


# --------------------------------------------------------------------------
# Reviewer-found defects in the first cut of inlining. Each one lets the prompt read as
# COMPLETE while something is missing from it, which is the exact failure inlining exists to
# stop -- just moved one level up.
# --------------------------------------------------------------------------
def test_an_added_path_is_marked_not_shown_when_another_path_is_inlined(repo):
    """An ADDED path never has a delta command (there is no old blob to diff against), so if
    another path IS inlined the prose says the diff is below while this file appears nowhere."""
    _write(repo, "a.md", "one\n")
    _stage(repo, "a.md")
    _audit(repo)

    _write(repo, "a.md", "two\n")          # inlines
    _write(repo, "new.md", "brand new\n")  # ADDED: nothing to diff against
    _stage(repo, "a.md", "new.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    added_row, = [ln for ln in block.splitlines() if "ADDED" in ln and "new.md" in ln]
    assert "not shown" in added_row.lower() or "re-read" in added_row.lower(), (
        "an ADDED path is invisible while the prompt claims the diff is below:\n" + block)


def test_a_changed_path_with_no_recoverable_blob_is_marked_when_another_is_inlined(repo,
                                                                                  monkeypatch):
    """Same hazard via the other route: no old blob means no command AND no inline."""
    import reviewer_prompts

    real = reviewer_prompts._blob_in_object_db

    def only_a(proj, sha):
        return real(proj, sha) and sha == only_a.keep

    # Distinct content on purpose: identical bytes get the SAME blob sha, so the two files would
    # be indistinguishable to a sha-keyed stub.
    _write(repo, "a.md", "alpha one\n")
    _write(repo, "b.md", "bravo one\n")
    _stage(repo, "a.md", "b.md")
    only_a.keep = rl.staged_blob_shas(repo)["a.md"]
    _audit(repo)

    _write(repo, "a.md", "alpha two\n")
    _write(repo, "b.md", "bravo two\n")
    _stage(repo, "a.md", "b.md")
    monkeypatch.setattr(reviewer_prompts, "_blob_in_object_db", only_a)

    block = rl.staged_change_block(repo, "claude-1", SID)
    b_row, = [ln for ln in block.splitlines() if "b.md" in ln and "CHANGED" in ln]
    assert "not shown" in b_row.lower() or "re-read" in b_row.lower(), (
        "a path with no delta and no inline is unmarked:\n" + block)


def test_a_runtime_diff_failure_is_not_reported_as_too_large(repo, monkeypatch):
    """Telling the reviewer a diff was 'too large' when git actually FAILED is false evidence:
    it implies the content exists and is merely big, so a size bump would surface it."""
    import reviewer_prompts

    monkeypatch.setattr(reviewer_prompts, "_delta_text",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("git exploded")))
    _write(repo, "a.md", "one\n")
    _stage(repo, "a.md")
    _audit(repo)
    _write(repo, "a.md", "two\n")
    _stage(repo, "a.md")

    block = rl.staged_change_block(repo, "claude-1", SID)
    assert "too large" not in block.lower(), (
        "a runtime failure was reported as a size rejection:\n" + block)
    assert "git diff" in block, "the command must survive a runtime failure"


def test_the_inline_cap_counts_utf8_bytes_not_characters(repo, monkeypatch):
    """The caps are documented as byte counts. `len()` on a str counts CHARACTERS, so multi-byte
    content would sail past a limit the operator set to bound prompt size.

    Stubs the diff text rather than crafting a file, so the assertion isolates the cap arithmetic
    instead of depending on how many characters git's own diff header happens to add.
    """
    import reviewer_prompts

    payload = "é" * 400     # 400 characters, 800 UTF-8 bytes
    monkeypatch.setattr(reviewer_prompts, "_delta_text", lambda *a, **k: payload)
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "600")   # between the two

    _write(repo, "a.md", "one\n")
    _stage(repo, "a.md")
    _audit(repo)
    _write(repo, "a.md", "two\n")
    _stage(repo, "a.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert payload not in block, (
        "a diff of 800 UTF-8 bytes was inlined under a 600-BYTE cap because len() counted "
        "its 400 characters")


def test_the_inline_caps_default_to_the_measured_sizes(monkeypatch):
    """The cap IS the fix, and the first one shipped was a guess that missed most of its target.

    Measured over 350 real re-review diffs recovered from two repos' audit histories: median
    19,883 bytes. A 6,000-byte cap inlined 17% of them, so 83% of re-reviews fell back to the
    command-only prompt -- which is the prompt that produced the stale restatements this module
    exists to stop. Worse, the correlation runs the wrong way: a big diff means a heavily
    rewritten file, exactly when the reviewer holds the most stale line-numbered findings.

    48,000 covers 75%; the tail past it is append-only logs reaching half a megabyte, which
    SHOULD stay excluded. These are measurements, not preferences -- re-measure before changing
    them. See DELTA_INLINE_PLAN.md, "The caps were guessed".
    """
    import reviewer_prompts

    seen = {}

    def capture(env_var, key, default, minimum):
        seen[key] = (env_var, default, minimum)
        return default

    monkeypatch.setattr(reviewer_prompts, "_tunable_int", capture)

    assert reviewer_prompts.delta_inline_max() == 48000
    assert reviewer_prompts.delta_inline_total_max() == 96000
    assert seen == {
        "deltaInlineMax": ("REVIEWER_DELTA_INLINE_MAX", 48000, 0),
        "deltaInlineTotalMax": ("REVIEWER_DELTA_INLINE_TOTAL_MAX", 96000, 0),
    }, "the caps must stay operator-overridable through env AND config, floored at the 0 sentinel"


# --------------------------------------------------------------------------
# Fix 3: the FALLBACK branches. Where a diff does not fit the cap the reviewer gets a command
# instead, and a command may be declined -- which is the original defect. These pin the two
# things the fallback prose did not say: that this is a RE-ATTEMPT, and that the fetch comes
# BEFORE forming a judgement.
# --------------------------------------------------------------------------
def _fallback_block(repo, monkeypatch, cap="40"):
    """A CHANGED path whose diff is over the per-path cap, so its row falls back to a command."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", cap)
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "".join("padding line {}\n".format(i) for i in range(200)))
    _stage(repo, "doc.md")
    return rl.staged_change_block(repo, "claude-1", SID)


def test_a_too_large_path_says_the_commit_was_reattempted(repo, monkeypatch):
    """The reviewer is a RESUMED session holding its own prior rejection. Told only that files
    changed, it can read the round as a continuation of the same submission; told the commit was
    RE-ATTEMPTED after the agent made changes, the staleness of its findings is the premise."""
    block = _fallback_block(repo, monkeypatch)
    low = block.lower()
    assert "re-attempt" in low or "reattempt" in low, (
        "nothing tells the reviewer this is a second attempt at the same commit:\n" + block)


def test_a_too_large_path_orders_the_fetch_before_judgement(repo, monkeypatch):
    """`RUN IT` is an instruction without an ordering, and 3 of 57 rounds ran nothing at all.
    The command must be placed BEFORE forming a judgement, not offered alongside one."""
    block = _fallback_block(repo, monkeypatch)
    low = block.lower()
    assert "before" in low, "no ordering directive at all:\n" + block
    assert "git diff" in block, "the ordering is worthless without the command it orders"


def test_the_no_delta_branch_also_orders_the_fetch_first(repo, monkeypatch):
    """Nothing inlined but commands available -- the pre-inlining prompt, still reachable whenever
    every diff misses the cap. It carried the weakest wording of the three."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "1")
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "changed\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    low = block.lower()
    assert "@@" not in block, "precondition: nothing should have been inlined here"
    assert "re-attempt" in low or "reattempt" in low, block
    assert "before" in low, block


def test_an_added_path_with_no_command_still_orders_the_reread_first(repo, monkeypatch):
    """An ADDED path has no old blob, so there is no diff to fetch and no command to run -- the
    frequent cause of the no-command branch, the pruned blob being the rare one. Its instruction
    is a full re-read, and that re-read needs the same ordering."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "brand_new.md", "entirely new file\n")
    _stage(repo, "brand_new.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    low = block.lower()
    assert "ADDED" in block, block
    assert "re-attempt" in low or "reattempt" in low, block
    assert "before" in low, block


def test_the_fallback_instruction_carries_the_literal_command_not_a_reference(repo, monkeypatch):
    """"Run the `delta:` command given for the path" makes the reviewer scan back up, match a
    label and reassemble -- indirection at the one moment we want none, and the rounds that
    actually skipped had a SINGLE path, so there was never any ambiguity to justify it. The
    command is cheap to repeat: put it where the instruction is."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "1")
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "changed\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    head, instruction = block.split("CHANGED/ADDED:", 1)
    assert "git diff" in head, "precondition: the row should carry the command"
    assert "git diff" in instruction, (
        "the instruction paragraph names no command, so the reviewer must go find it:\n" + block)


def test_the_literal_commands_are_given_for_every_path_that_needs_one(repo, monkeypatch):
    """With several paths each command differs by blob sha, so listing one would be worse than
    listing none. Every path that has a command gets it."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "1")
    _write(repo, "a.md", "a one\n")
    _write(repo, "b.md", "b one\n")
    _stage(repo, "a.md", "b.md")
    _audit(repo)
    _write(repo, "a.md", "a two\n")
    _write(repo, "b.md", "b two\n")
    _stage(repo, "a.md", "b.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    _, instruction = block.split("CHANGED/ADDED:", 1)
    assert instruction.count("git diff") >= 2, (
        "only some paths' commands reached the instruction:\n" + block)
    assert ":a.md" in instruction and ":b.md" in instruction, block


def test_a_removed_only_rereview_emits_no_empty_command_list(repo):
    """Unstaging a path is movement, so the block is emitted -- but nothing CHANGED and nothing
    was ADDED, so the no-delta branch had no paths to build commands from. It still printed its
    `BEFORE ... run:` header above an empty list, ordering the reviewer to run nothing and
    announcing changed files that do not exist."""
    _write(repo, "keep.md", "keep\n")
    _write(repo, "drop.md", "drop\n")
    _stage(repo, "keep.md", "drop.md")
    _audit(repo)

    subprocess.run(["git", "-C", repo, "rm", "-q", "--cached", "drop.md"], check=True)
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "REMOVED" in block, "precondition: the unstaged path should be reported:\n" + block
    assert "run:\n\n" not in block and not block.rstrip().endswith("run:"), (
        "an empty command list was emitted:\n" + block)
    assert "CHANGED/ADDED:" not in block, (
        "nothing changed and nothing was added, yet the block says otherwise:\n" + block)


def test_the_pi_backend_is_not_told_its_changed_paths_are_added_or_pruned(repo, monkeypatch):
    """The no-delta branch may say no delta is shown; it may not say WHY in terms that exclude
    the reader's own reason. Prose enumerating "ADDED, or a pruned blob" tells pi a plain fact
    about ordinary changed files that is false.

    Inlining is DISABLED here on purpose. When it was written, `_delta_commands` returning `{}`
    for pi meant every ordinary CHANGED path reached this branch, so an ordinary change was
    enough to exercise it. Fix 4 gives pi the inlined diff, and that same setup now lands in the
    inlined branch -- where all three assertions pass without testing anything. Disabling the
    inline puts pi back in the branch this test exists for."""
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")
    _write(repo, "doc.md", "start\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "changed\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "CHANGED" in block and "git diff" not in block, (
        "precondition: pi gets a CHANGED row and no delta command:\n" + block)
    low = block.lower()
    assert "pruned" not in low, (
        "an ordinary changed path is described as having a pruned blob:\n" + block)
    assert "no earlier version to diff against" not in low, (
        "an ordinary changed path is described as ADDED:\n" + block)


def test_a_partly_inlined_block_never_says_no_fetch_is_needed(repo, monkeypatch):
    """`You do not need to fetch anything` is true only when EVERY path was inlined.

    With one path inlined and one over the cap the block asserted it in the lead paragraph and
    then, further down, told the reviewer that path was missing and had to be fetched. A reviewer
    that believes the first sentence never reaches the second -- which is precisely the
    reads-as-complete-when-it-is-not failure that inlining exists to stop.
    """
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "3000")
    _write(repo, "small.md", "one\n")
    _write(repo, "big.md", "start\n")
    _stage(repo, "small.md", "big.md")
    _audit(repo)

    _write(repo, "small.md", "two\n")                       # inlines
    _write(repo, "big.md", "".join("padding line {}\n".format(i) for i in range(200)))
    _stage(repo, "small.md", "big.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "precondition: the small path should have inlined"
    assert "too large" in block.lower(), "precondition: the big path should NOT have inlined"
    assert "do not need to fetch" not in block.lower(), (
        "the block claims nothing needs fetching while withholding a path:\n" + block)


def test_a_fully_inlined_block_still_says_no_fetch_is_needed(repo):
    """The counterpart, so the fix above is a CONDITION and not a deletion: when everything is in
    the prompt, saying so is what stops a pointless fetch."""
    _write(repo, "doc.md", "one\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "two\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "precondition: this should have inlined"
    assert "do not need to fetch" in block.lower(), block


def test_every_fallback_branch_states_the_consequence_of_answering_from_memory(repo, monkeypatch):
    """The inlined branch already says answering from an earlier draft is how an already-fixed
    finding gets re-raised. The fallback branches need it MORE -- they are the ones where the
    reviewer has to choose to go look."""
    block = _fallback_block(repo, monkeypatch)
    low = block.lower()
    assert "already-fixed" in low or "already fixed" in low, (
        "the fallback never names the failure mode it is trying to prevent:\n" + block)


# --------------------------------------------------------------------------
# pi: the diff is INLINED for it too, and no command it cannot run is ever named.
#
# pi has no shell. Its `git_read` tool builds `git show --stat <ref>` -- stat only, never file
# content -- and fences refs to [A-Za-z0-9._/@~^=-], which excludes the `:` of an index spec. So
# `git show :<path>` is doubly unrunnable there, and `git diff <blob> :<path>` is not expressible
# at all, since the staged form hard-codes --cached and resolves its ref as a tree-ish.
#
# Inlining needs none of that: the hook runs git in its OWN process and hands over text. pi got
# nothing only because the inline candidate set was the codex-only command map.
#
# Every assertion below reads the WHOLE block, which is sound because these fixtures write
# content that contains neither `git show` nor `git diff`. A payload is arbitrary staged content
# and may legitimately carry either string -- a diff of this repo's own source does -- so a hit
# in these blocks can only have come from generated prose.
# --------------------------------------------------------------------------
@pytest.fixture
def pi(monkeypatch):
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")


def _assert_names_no_unusable_command(block):
    assert "git show" not in block, (
        "pi is told to run `git show`, which its git_read builds as --stat only and whose `:` "
        "ref it rejects outright:\n" + block)
    assert _delta_commands(block) == [], (
        "pi is given a `git diff <blob> :<path>`, which git_read cannot express at all:\n" + block)


def test_pi_gets_the_diff_inlined(repo, pi):
    """The fix itself. A changed path's diff belongs IN pi's prompt, where it cannot be skipped,
    rather than behind an instruction pi has no way to carry out."""
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "pi got no inlined diff:\n" + block
    assert "+beta" in block, "the diff is announced but its content is absent:\n" + block
    _assert_names_no_unusable_command(block)


def test_pi_over_the_cap_names_the_withheld_path_but_no_command(repo, pi, monkeypatch):
    """The residual branch. A path too large to inline must still be NAMED -- a block that
    withholds content silently reads as complete when it is not -- but naming it must not come
    with a command pi cannot run."""
    # 3000, not a tighter-looking number: the SMALL path must still inline, and a one-line change
    # already costs ~130 bytes of diff headers, so a cap chosen to look small excludes BOTH paths
    # and the test quietly stops covering the partly-inlined branch it exists for.
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "3000")
    _write(repo, "small.md", "alpha\n")
    _write(repo, "big.md", "alpha\n")
    _stage(repo, "small.md", "big.md")
    _audit(repo)
    _write(repo, "small.md", "beta\n")
    _write(repo, "big.md", "".join("padding line {}\n".format(i) for i in range(200)))
    _stage(repo, "small.md", "big.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "precondition: the small path should have inlined:\n" + block
    assert "big.md" in block, "the withheld path is not named at all:\n" + block
    _assert_names_no_unusable_command(block)


def test_pi_with_only_an_added_path_orders_a_rederive_and_names_no_command(repo, pi):
    """No candidate exists at all -- an ADDED path has no earlier blob to diff against -- so
    nothing can be inlined and the branch is pure instruction. That is exactly where a false
    command does the most damage, since it is the only thing the reviewer is given."""
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "fresh.md", "beta\n")
    _stage(repo, "fresh.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    low = block.lower()
    assert "re-attempt" in low or "reattempt" in low, (
        "nothing tells pi this is a second attempt at the same commit:\n" + block)
    assert "before" in low, "no ordering directive at all:\n" + block
    _assert_names_no_unusable_command(block)


def test_pi_with_inlining_disabled_still_names_no_unusable_command(repo, pi, monkeypatch):
    """`0` rolls back INLINING. It must not roll back the backend-awareness of the prose: the
    command is wrong for pi whether or not inlining is on, so restoring it would make the
    documented escape hatch reintroduce the false instruction it exists to escape."""
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" not in block, "precondition: inlining is disabled, nothing should be inlined"
    assert "CHANGED" in block, "precondition: the changed path should still be reported"
    _assert_names_no_unusable_command(block)


def test_pi_is_never_handed_a_diff_against_an_unresolvable_blob(repo, pi, monkeypatch):
    """The object-DB filter is the reason `git diff <blob>` is safe to name, and it must keep
    guarding the INLINE path now that inlining no longer runs through the command map. An
    unreferenced loose object is prunable; diffing against one errors."""
    monkeypatch.setattr(reviewer_prompts := __import__("reviewer_prompts"),
                        "_blob_in_object_db", lambda proj, sha: False)
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" not in block, (
        "a diff was inlined for a blob that cannot be resolved:\n" + block)
    assert "CHANGED" in block, "precondition: the changed path should still be reported"
    _assert_names_no_unusable_command(block)


def test_pis_inlined_branch_does_not_tell_it_to_read_a_whole_staged_file(repo, pi):
    """The secondary clause offers a fuller read when the diff does not settle a question. For
    codex that is `git show :<path>`; pi has no per-file staged read at all, so the clause must
    become the staged patch rather than substituting a phrase into prose written around a
    command.

    The first cut did exactly that and rendered `Read the whole staged file (the staged patch
    (git_read ...))` -- a nested parenthetical that also ordered a read pi cannot perform. Every
    test was green; it was found by printing the block."""
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "precondition: this should have inlined:\n" + block
    assert "whole staged file" not in block, (
        "pi is told to read a whole staged file, which it has no way to do:\n" + block)
    assert "((" not in block and "(the staged patch (" not in block, (
        "a route phrase was substituted into prose built around a command:\n" + block)
    assert "staged patch" in block, (
        "pi is offered no fuller read at all, so the diff is its only recourse:\n" + block)


# --------------------------------------------------------------------------
# claude: the same asymmetry as pi, one step further. It has no git tool AT ALL, so neither
# codex's `git show :<path>` nor pi's `git_read` call may appear in its prose -- and unlike pi
# there is nothing to point it at, because the gate puts the whole staged patch in the prompt.
# --------------------------------------------------------------------------
@pytest.fixture
def claude(monkeypatch):
    monkeypatch.setenv("REVIEWER_BACKEND", "claude")


def _assert_names_no_tool_claude_lacks(block):
    assert "git show" not in block, (
        "claude is told to run `git show`, and it has no shell:\n" + block)
    assert "git_read" not in block, (
        "claude is told to call git_read, which is pi's extension and does not exist for "
        "it:\n" + block)
    assert _delta_commands(block) == [], (
        "claude is given a `git diff` command, and it has no shell:\n" + block)


def test_claude_gets_the_diff_inlined_and_no_command(repo, claude):
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" in block, "claude got no inlined diff:\n" + block
    assert "+beta" in block, "the diff is announced but its content is absent:\n" + block
    _assert_names_no_tool_claude_lacks(block)


def test_claude_with_only_an_added_path_is_pointed_at_the_patch_in_its_prompt(repo, claude):
    """Pure instruction, no candidate to inline -- the branch where a false tool name is the
    only thing the reviewer is given. pi is sent to git_read here; claude has to be sent to the
    patch already in front of it."""
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "fresh.md", "beta\n")
    _stage(repo, "fresh.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    _assert_names_no_tool_claude_lacks(block)
    assert "staged patch" in block.lower(), (
        "claude is not pointed at anything it can actually consult:\n" + block)


def test_claude_with_inlining_disabled_still_names_no_unusable_tool(repo, claude, monkeypatch):
    monkeypatch.setenv("REVIEWER_DELTA_INLINE_MAX", "0")
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    assert "@@" not in block, "precondition: inlining is disabled, nothing should be inlined"
    assert "CHANGED" in block, "precondition: the changed path should still be reported"
    _assert_names_no_tool_claude_lacks(block)


def test_claudes_inlined_branch_does_not_tell_it_to_read_a_whole_staged_file(repo, claude):
    """The "if the diff does not settle it" clause. codex reads the indexed file, pi consults
    the patch it fetches; claude consults the patch it was handed, and the clause must not
    render as a nested parenthetical or name a per-file staged read it does not have."""
    _write(repo, "doc.md", "alpha\n")
    _stage(repo, "doc.md")
    _audit(repo)
    _write(repo, "doc.md", "beta\n")
    _stage(repo, "doc.md")
    block = rl.staged_change_block(repo, "claude-1", SID)

    _assert_names_no_tool_claude_lacks(block)
    assert "whole staged file" not in block.lower(), (
        "claude is told to read a whole staged file, which it cannot do:\n" + block)
    assert "((" not in block, "a nested parenthetical rendered:\n" + block
