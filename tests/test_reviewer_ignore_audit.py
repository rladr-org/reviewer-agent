# SPDX-License-Identifier: MPL-2.0
"""Audit-record fields for `reviewerAgent-ignore.md`. The reviewer log line and audit
record gain `ignore_matched_files` and `ignore_matched_lines`, so an operator scanning the
log can see the exclusion mechanism working (e.g. `staged_files=1003 staged_lines=464550
ignore_matched_files=987 ignore_matched_lines=459812 ...`).

Both counters are 0 when no ignore file is present, and 0 when the file is present but no
staged path matches. See docs/plans/reviewer_ignore_guidance.md, "Audit-record visibility"."""
import subprocess
from pathlib import Path

import reviewer_audit as ra
import reviewer_lib as rl

from reviewer_ignore import _SelfExemptedMatcher, parse_reviewer_ignore
from reviewer_ignore_match import IgnoreMatcher


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)


def _stage(repo, path, content):
    p = Path(repo) / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    subprocess.run(["git", "-C", repo, "add", path], check=True)


# --------------------------------------------------------------------------
# matched_counts helper — returns (files, lines) for staged paths that match the matcher.
# --------------------------------------------------------------------------
def test_matched_counts_returns_zero_when_matcher_is_none(tmp_path):
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    _stage(repo, "src.py", "x = 1\n")
    assert rl.matched_counts(repo, None) == (0, 0)


def test_matched_counts_returns_zero_when_no_path_matches(tmp_path):
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    _stage(repo, "src.py", "x = 1\n")
    matcher = _SelfExemptedMatcher(IgnoreMatcher([(1, "data/**")]))
    assert rl.matched_counts(repo, matcher) == (0, 0)


def test_matched_counts_sums_added_and_deleted_lines_for_matched_paths(tmp_path):
    """`git diff --cached --numstat` prints `<added>\\t<deleted>\\t<path>`. matched_counts
    sums added+deleted for paths the matcher accepts, and ignores paths it rejects."""
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    _stage(repo, "data/a.jsonl", "row1\nrow2\nrow3\n")     # 3 added lines
    _stage(repo, "data/b.jsonl", "onlyone\n")               # 1 added line
    _stage(repo, "src.py", "x = 1\ny = 2\n")               # 2 added lines (NOT matched)
    matcher = _SelfExemptedMatcher(IgnoreMatcher([(1, "data/**")]))
    files, lines = rl.matched_counts(repo, matcher)
    assert files == 2
    assert lines == 4   # 3 + 1, src.py not counted


# --------------------------------------------------------------------------
# Audit record: the two counters appear on record_commit_attempt's dict AND are 0 when no
# ignore file is present, keeping today's behavior visible in the record for audits
# without an ignore mechanism engaged.
# --------------------------------------------------------------------------
def test_audit_record_has_zero_ignore_counts_by_default(tmp_path, monkeypatch):
    """No ignore_matcher argument -> ignore_matched_files and ignore_matched_lines are 0.
    Field is present, so a downstream log-line formatter using audit.get(field) never sees
    None (which would render as `None` in the log)."""
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    _stage(repo, "src.py", "x = 1\n")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    rec = ra.record_commit_attempt(
        proj=repo, claude_session="c1", reviewer_session="sid-1", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None,
    )
    assert rec is not None
    assert rec["ignore_matched_files"] == 0
    assert rec["ignore_matched_lines"] == 0


def test_audit_record_reports_counts_from_the_matcher(tmp_path, monkeypatch):
    """When ignore_matcher is passed to record_commit_attempt, the record's counters name
    the excluded set. Uses matched_counts under the hood, so the shapes match that helper."""
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    _stage(repo, "data/a.jsonl", "row1\nrow2\nrow3\n")
    _stage(repo, "src.py", "x = 1\n")
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.set_active(repo)
    matcher = _SelfExemptedMatcher(IgnoreMatcher([(1, "data/**")]))
    rec = ra.record_commit_attempt(
        proj=repo, claude_session="c1", reviewer_session="sid-1", backend="codex",
        prompt="P", code=0, out="APPROVED", err="", completed=True, approved=True,
        verdict_file=None, ignore_matcher=matcher,
    )
    assert rec is not None
    assert rec["ignore_matched_files"] == 1     # only data/a.jsonl matched
    assert rec["ignore_matched_lines"] == 3     # 3 added lines
    # And src.py's 1 line still shows up in staged_lines (unfiltered).
    assert rec["staged_files"] == 2
    assert rec["staged_lines"] == 4              # 3 + 1


def test_matched_counts_binary_file_contributes_a_file_not_lines(tmp_path):
    """numstat prints `-` for binary files. matched_counts must not raise on int('-') and
    counts the file but zero lines."""
    repo = str(tmp_path / "r")
    Path(repo).mkdir()
    _init_repo(repo)
    (Path(repo) / "data").mkdir()
    (Path(repo) / "data/blob.bin").write_bytes(b"\x00\x01\x02\xff" * 8)
    subprocess.run(["git", "-C", repo, "add", "data/blob.bin"], check=True)
    matcher = _SelfExemptedMatcher(IgnoreMatcher([(1, "data/**")]))
    files, lines = rl.matched_counts(repo, matcher)
    assert files == 1
    assert lines == 0
