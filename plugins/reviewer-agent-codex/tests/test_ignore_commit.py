# SPDX-License-Identifier: MPL-2.0
"""Native Codex ignore behavior through real Git and a stub reviewer."""
import subprocess

import pytest

from reviewer_codex import commit_tool, state

IGNORE = "reviewerAgent-ignore.md"
RULES = "# Generated records\n```reviewer-ignore\ndata/**\n```\n"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def stage(repo, name, content):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    git(repo, "add", "--", name)


def configure(repo, rules=RULES):
    stage(repo, IGNORE, rules)
    git(repo, "commit", "-qm", "configure")


@pytest.fixture
def reviewer(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "reviewer")
    calls = []

    def review(cwd, session_id, role, sandbox, prompt, timeout, **kwargs):
        calls.append(prompt)
        state.publish_reviewer_session(cwd, session_id, role, "ignore-review")
        return 0, "REJECTED:\n1. Fix source", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", review)
    return calls


def audit(repo):
    return commit_tool.reviewed_commit(repo, "test change",
                                      state.create_commit_ticket(repo, "ignore-session"))


@pytest.mark.parametrize("bad", ["```reviewer-ignore\n!bad\n```\n", b"\xff"])
@pytest.mark.parametrize("existing", [False, True])
def test_malformed_candidate_blocks_before_backend(git_repo, reviewer, bad, existing):
    if existing:
        configure(git_repo)
    stage(git_repo, IGNORE, bad)
    before = git(git_repo, "rev-parse", "HEAD")
    result = audit(git_repo)
    assert result["status"] == "error"
    assert IGNORE in result["message"]
    assert "staged" in result["message"]
    assert reviewer == []
    assert git(git_repo, "rev-parse", "HEAD") == before


def test_first_introduction_does_not_supply_prose_or_matches(git_repo, reviewer):
    stage(git_repo, IGNORE, RULES)
    stage(git_repo, "data/rows.jsonl", "one\n")
    result = audit(git_repo)
    assert RULES not in reviewer[0]
    assert result["ignore_matched_files"] == 0
    assert result["ignore_matched_lines"] == 0


def test_effective_guidance_and_counts_on_fresh_and_retry_audits(git_repo, reviewer):
    configure(git_repo)
    stage(git_repo, "data/rows.jsonl", "one\ntwo\n")
    stage(git_repo, "source.py", "a = 1\n")
    for _ in range(2):
        result = audit(git_repo)
        prompt = reviewer[-1]
        assert RULES.rstrip() in prompt
        assert "[reviewerAgent-ignore.md]" in prompt
        assert "schema" in prompt
        assert "git diff --cached -- <path>" in prompt
        assert result["ignore_matched_files"] == 1
        assert result["ignore_matched_lines"] == 2
        history = state.review_history(git_repo)[-1].read_text()
        assert "ignore_matched_files=1" in history
        assert "ignore_matched_lines=2" in history
        # Statistics must not corrupt the saved verdict used for retry findings.
        assert state.last_review_file(git_repo, "ignore-session").read_text() == result["review"]


def test_retry_skips_delta_fetch_but_preserves_inventory(git_repo, reviewer, monkeypatch):
    configure(git_repo)
    stage(git_repo, "data/rows.jsonl", "old record\n")
    stage(git_repo, "source.py", "old_source = 1\n")
    audit(git_repo)
    stage(git_repo, "data/rows.jsonl", "SECRET_NEW_RECORD\n")
    stage(git_repo, "source.py", "new_source = 2\n")
    original = commit_tool._delta
    fetched = []

    def delta(cwd, sha, path):
        fetched.append(path)
        return original(cwd, sha, path)

    monkeypatch.setattr(commit_tool, "_delta", delta)
    audit(git_repo)
    prompt = reviewer[-1]
    assert fetched == ["source.py"]
    assert "CHANGED: data/rows.jsonl" in prompt
    assert "SKIM" in prompt
    assert "SECRET_NEW_RECORD" not in prompt
    assert "+new_source = 2" in prompt
    cursor = state.read_commit_cursor(git_repo, "ignore-session")
    assert "data/rows.jsonl" in cursor["files"]


def test_changed_and_deleted_rules_use_old_prose(git_repo, reviewer):
    configure(git_repo)
    stage(git_repo, IGNORE, "# NEW RULES\n```reviewer-ignore\n**\n```\n")
    stage(git_repo, "source.py", "a = 1\n")
    assert audit(git_repo)["ignore_matched_files"] == 0
    assert RULES.rstrip() in reviewer[-1]
    assert "# NEW RULES" not in reviewer[-1]
    git(git_repo, "rm", "-f", "--", IGNORE)
    audit(git_repo)
    # Retry may inline the ignore file's delta; examine the guidance block only.
    guidance = reviewer[-1].split("[reviewerAgent-ignore.md]", 1)[1].split(
        "[/reviewerAgent-ignore.md]", 1)[0]
    assert RULES.rstrip() in guidance


def test_ignore_file_itself_is_inlined_on_retry(git_repo, reviewer):
    configure(git_repo, "```reviewer-ignore\n**\n```\n")
    stage(git_repo, IGNORE, "# FIRST\n```reviewer-ignore\n**\n```\n")
    audit(git_repo)
    stage(git_repo, IGNORE, "# SECOND\n```reviewer-ignore\n**\n```\n")
    result = audit(git_repo)
    assert "+# SECOND" in reviewer[-1]
    assert result["ignore_matched_files"] == 0


def test_all_matched_files_still_require_approval_and_are_committed(
        git_repo, reviewer, monkeypatch):
    configure(git_repo)
    stage(git_repo, "data/rows.jsonl", "one\n")
    before = git(git_repo, "rev-parse", "HEAD")
    assert audit(git_repo)["status"] == "rejected"
    assert git(git_repo, "rev-parse", "HEAD") == before
    original = commit_tool.backend.review_turn

    def approve(*args, **kwargs):
        original(*args, **kwargs)
        return 0, "APPROVED", ""

    monkeypatch.setattr(commit_tool.backend, "review_turn", approve)
    result = audit(git_repo)
    assert result["status"] == "committed"
    assert len(reviewer) == 2
    assert result["ignore_matched_files"] == 1
    assert git(git_repo, "show", "HEAD:data/rows.jsonl") == "one"


def test_absent_file_has_zero_counts(git_repo, reviewer):
    stage(git_repo, "source.py", "a = 1\n")
    result = audit(git_repo)
    assert "[reviewerAgent-ignore.md]" not in reviewer[0]
    assert result["ignore_matched_files"] == result["ignore_matched_lines"] == 0


@pytest.mark.parametrize("configured", [False, True])
def test_mandatory_audit_contract_precedes_ignore_guidance(git_repo, reviewer, configured):
    from reviewer_codex import prompts

    if configured:
        configure(git_repo)
    stage(git_repo, "source.py", "a = 1\n")
    for _ in range(2):
        audit(git_repo)
        prompt = reviewer[-1]
        guidance = ("[reviewerAgent-ignore.md]" if configured else
                    "No effective reviewer-ignore guidance for this audit")
        assert prompt.index(prompts.COMMIT_PROMPT) < prompt.index(guidance)


def test_malformed_ignore_outcome_has_zero_counts_in_result_and_history(git_repo, reviewer):
    stage(git_repo, IGNORE, "```reviewer-ignore\n!bad\n```\n")
    result = audit(git_repo)
    assert result["status"] == "error"
    assert reviewer == []
    assert result["ignore_matched_files"] == result["ignore_matched_lines"] == 0
    history = state.review_history(git_repo)[-1].read_text()
    assert "OUTCOME: FAILED" in history
    assert "ignore_matched_files=0" in history
    assert "ignore_matched_lines=0" in history
