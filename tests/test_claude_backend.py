# SPDX-License-Identifier: MPL-2.0
"""The `claude` reviewer backend: a Claude Code child, constrained to fail closed.

This backend exists so a Claude Code user can run an independent reviewer on the model of their
choice without installing Codex or Pi. It shells out to the `claude` CLI they already have and
lets it authenticate itself -- no credential is ever read, copied or forwarded by this package.

Three properties are load-bearing, and each was established by running the real CLI rather than
by reading its documentation:

  NO RECURSION.  A child launched without `--safe-mode` fired the operator's own hooks 19 times
                 in one measured run: SessionStart briefed a reviewer, then the Stop hook looped
                 the child until the nudge cap, and the review returned empty. `--safe-mode`
                 disables hooks, plugins, MCP and CLAUDE.md, and (unlike `--bare`) keeps
                 subscription authentication working.

  FAIL CLOSED.   `--permission-mode auto` is NOT enough. Its allowlist is not exclusive: an
                 unnamed tool goes to the classifier, which approved a file write in testing,
                 and the model located writers at runtime through `ToolSearch` and tried to
                 delegate through subagents. `dontAsk` denies everything not explicitly
                 allowed -- including tools nobody enumerated and tools a later version adds --
                 and held across repeated attempts by a model actively trying to escape it.

  NO GIT ACCESS NEEDED.  Claude's `Read` sees the working tree, not the index, and granting
                 shell access to reach the index would reintroduce the `diff.external`
                 execution hole the Pi extension had to close. The staged patch is inlined into
                 the prompt instead, so the reviewer needs no shell at all.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_backends
import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
PROJ = "/Users/x/proj"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_BACKEND", raising=False)
    return tmp_path


# --------------------------------------------------------------------------
# the backend is selectable and carries its own model and effort
# --------------------------------------------------------------------------
def test_claude_is_a_selectable_backend(home):
    assert rl.is_valid_backend("claude")
    rl.set_project_backend(PROJ, "claude")
    assert rl.reviewer_backend(PROJ) == "claude"


def test_the_model_slot_is_separate_from_codex_and_pi(home):
    rl.set_backend_model("claude", "opus")
    rl.set_backend_model("pi", "some-pi-model")

    assert rl.reviewer_model("claude") == "opus"
    assert rl.reviewer_model("pi") == "some-pi-model"
    assert rl.reviewer_model("codex") is None
    assert rl.reviewer_model_detail("claude")[1].endswith("claude_model")


def test_the_environment_override_wins_and_names_itself(home, monkeypatch):
    rl.set_backend_model("claude", "sonnet")
    monkeypatch.setenv("REVIEWER_CLAUDE_MODEL", "fable")

    model, source = rl.reviewer_model_detail("claude")

    assert model == "fable"
    assert "REVIEWER_CLAUDE_MODEL" in source


def test_effort_levels_are_the_ones_the_cli_accepts(home):
    # The CLI takes low|medium|high|xhigh|max. Codex's `none`/`ultra` are not among them, and
    # sending one fails the call under a fail-closed gate.
    assert set(rl.reviewer_efforts("claude")) == {"low", "medium", "high", "xhigh", "max"}
    for level in rl.reviewer_efforts("claude"):
        assert rl.is_valid_backend_effort("claude", level)
    for rejected in ("none", "ultra", "off", "MAX", ""):
        assert not rl.is_valid_backend_effort("claude", rejected)


def test_an_unset_effort_resolves_to_a_stated_default(home):
    effort, source = rl.reviewer_effort_detail("claude")

    assert effort in rl.reviewer_efforts("claude")
    assert source == "default"


def test_sessions_are_project_scoped_and_distinct_from_the_other_backends(home, tmp_path):
    rl.write_claude_session(PROJ, "session-1", "uuid-a")
    rl.write_session_id(PROJ, "session-1", "codex-thread")
    rl.write_pi_session(PROJ, "session-1", "pi-thread")

    assert rl.read_claude_session(PROJ, "session-1") == "uuid-a"
    assert rl.read_session_id(PROJ, "session-1") == "codex-thread"
    assert rl.read_pi_session(PROJ, "session-1") == "pi-thread"
    assert rl.claude_session_file(PROJ, "session-1", rl.ROLE_STOP) != rl.claude_session_file(
        PROJ, "session-1", rl.ROLE_REVIEWER
    )
    assert rl.read_claude_session("/Users/x/other", "session-1") is None


def test_rotation_clears_the_claude_slot_too(home):
    rl.write_claude_session(PROJ, "session-1", "uuid-a")
    rl.write_claude_session(PROJ, "session-1", "uuid-stop", role=rl.ROLE_STOP)

    rl.rotate_session(PROJ, "session-1")

    assert rl.read_claude_session(PROJ, "session-1") is None
    assert rl.read_claude_session(PROJ, "session-1", role=rl.ROLE_STOP) is None


# --------------------------------------------------------------------------
# the argv: every flag here is load-bearing
# --------------------------------------------------------------------------
def test_the_first_turn_supplies_its_own_session_id(home):
    argv = reviewer_backends.claude_argv("prompt text", session_id="11111111-2222-3333-4444-555555555555")

    assert "--session-id" in argv
    assert argv[argv.index("--session-id") + 1] == "11111111-2222-3333-4444-555555555555"
    assert "--resume" not in argv


def test_a_later_turn_resumes_that_session(home):
    argv = reviewer_backends.claude_argv(
        "prompt text", session_id="11111111-2222-3333-4444-555555555555", resume=True
    )

    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "11111111-2222-3333-4444-555555555555"
    assert "--session-id" not in argv


def test_safe_mode_is_always_passed(home):
    # Measured: without it, a child fires the operator's hooks and the stop supervisor loops it.
    assert "--safe-mode" in reviewer_backends.claude_argv("p", session_id="s")


def test_the_permission_mode_fails_closed(home):
    argv = reviewer_backends.claude_argv("p", session_id="s")

    # `auto` was measured letting a write through; `dontAsk` denies anything not allowlisted.
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "auto" not in argv


def test_only_reading_tools_are_allowed_and_the_mutating_ones_are_denied(home):
    argv = reviewer_backends.claude_argv("p", session_id="s")
    allowed = argv[argv.index("--allowedTools") + 1:argv.index("--disallowedTools")]
    denied = set(argv[argv.index("--disallowedTools") + 1:])

    assert set(allowed) <= {"Read", "Grep", "Glob"}
    assert {"Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch"} <= denied


def test_the_model_and_effort_reach_the_command_line(home):
    rl.set_backend_model("claude", "opus")
    rl.set_backend_effort("claude", "xhigh")

    argv = reviewer_backends.claude_argv("p", session_id="s")

    assert argv[argv.index("--model") + 1] == "opus"
    assert argv[argv.index("--effort") + 1] == "xhigh"


def test_an_unset_model_lets_the_cli_choose(home):
    argv = reviewer_backends.claude_argv("p", session_id="s")

    assert "--model" not in argv


def test_the_prompt_is_passed_on_stdin_not_in_argv(home):
    # A staged patch can be megabytes; an argv has a hard size limit, and a diff on the command
    # line would also leak into the process table.
    argv = reviewer_backends.claude_argv("SECRET PROMPT TEXT", session_id="s")

    assert "SECRET PROMPT TEXT" not in " ".join(argv)


# --------------------------------------------------------------------------
# reading the answer back
# --------------------------------------------------------------------------
def test_the_verdict_is_read_from_the_final_result_entry():
    stream = json.dumps([
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "APPROVED", "session_id": "abc"},
    ])

    assert reviewer_backends.parse_claude_result(stream) == "APPROVED"


def test_a_single_object_response_is_read_too():
    # `--output-format json` returns one object when the CLI is not in verbose mode, and an
    # array of events when it is. Both shapes are real; both are read.
    stream = json.dumps({"type": "result", "is_error": False, "result": "REJECTED:\n1. x"})

    assert reviewer_backends.parse_claude_result(stream).startswith("REJECTED")


def test_unparseable_output_yields_no_verdict_rather_than_a_guess():
    for junk in ("", "not json", "[]", json.dumps([{"type": "system"}])):
        assert reviewer_backends.parse_claude_result(junk) == ""


def test_a_rate_limited_run_is_recognised_and_named():
    stream = json.dumps([
        {"type": "rate_limit_event", "rate_limit_info": {"status": "exhausted"}},
        {"type": "result", "is_error": True,
         "result": "Claude usage limit reached. Your limit resets at 4pm.",
         "session_id": "abc"},
    ])

    detail = reviewer_backends.claude_rate_limit_detail(stream)

    assert detail
    assert "limit" in detail.lower()


def test_the_quota_report_on_an_ordinary_run_is_not_a_rate_limit():
    """MEASURED 2026-08-21: the CLI emits a `rate_limit_event` on EVERY run, with
    `status: "allowed"`. The first version of this detector keyed on the event's presence, and
    the live matrix turned a perfectly good APPROVED into "usage limit reached, commit blocked" --
    a fail-closed gate blocking on the evidence that nothing was wrong."""
    stream = json.dumps([
        {"type": "rate_limit_event",
         "rate_limit_info": {"status": "allowed", "resetsAt": 1787361000,
                             "overageStatus": "rejected"}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "APPROVED"},
    ])

    assert reviewer_backends.claude_rate_limit_detail(stream) is None


def test_a_successful_verdict_is_never_reclassified_as_a_rate_limit():
    """The reviewer audits diffs, and a diff can contain the words. Its own approval must not be
    convertible into a block by quoting them."""
    stream = json.dumps([
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "APPROVED\n\nSUGGESTIONS:\n1. The retry loop ignores the API rate limit "
                   "header; consider honouring it."},
    ])

    assert reviewer_backends.claude_rate_limit_detail(stream) is None


def test_an_ordinary_failure_is_not_reported_as_a_rate_limit():
    stream = json.dumps([
        {"type": "result", "is_error": True, "result": "some other failure"},
    ])

    assert reviewer_backends.claude_rate_limit_detail(stream) is None


# --------------------------------------------------------------------------
# the gate, driven end to end against a stand-in binary
# --------------------------------------------------------------------------
def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    (repo / "f.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
    (repo / "f.txt").write_text("two\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "f.txt"], check=True)
    return str(repo)


def run_commit_hook(repo, state, extra=None):
    env = dict(os.environ)
    env.update(
        REVIEWER_HOME=str(state),
        REVIEWER_STATE_DIR=str(state),
        REVIEWER_BACKEND="claude",
        REVIEWER_CLAUDE_BIN=str(Path(__file__).resolve().parent / "fake_claude.py"),
    )
    env.pop("REVIEWER_CONFIG_FILE", None)
    env.update(extra or {})
    payload = {
        "cwd": repo,
        "session_id": "claude-session",
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m x"},
    }
    process = subprocess.run(
        [sys.executable, str(ROOT / "commit_hook.py")], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    return process, (json.loads(process.stdout) if process.stdout.strip() else {})


def test_an_approving_claude_review_commits_it_and_denies_the_call(home, tmp_path):
    # The gate performs the reviewed commit itself and DENIES the tool call, so the agent's own
    # `git commit` never runs a second time. The denial names the published commit id.
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    before = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                            capture_output=True, text=True).stdout.strip()

    _process, output = run_commit_hook(repo, state, {"FAKE_CLAUDE_RESULT": "APPROVED"})

    hso = output.get("hookSpecificOutput") or {}
    assert hso.get("permissionDecision") == "deny"
    head = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head != before  # a real commit landed
    assert head[:12] in hso.get("permissionDecisionReason", "")  # the denial names it


def test_a_rejecting_claude_review_blocks_it(home, tmp_path):
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)

    _process, output = run_commit_hook(
        repo, state, {"FAKE_CLAUDE_RESULT": "REJECTED:\n1. f.txt:1 needs a newline."}
    )

    decision = (output.get("hookSpecificOutput") or {}).get("permissionDecision")
    reason = (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    assert decision == "deny"
    assert "needs a newline" in reason


def test_the_staged_patch_is_handed_over_rather_than_fetched(home, tmp_path):
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    prompt_dump = tmp_path / "prompt.txt"

    run_commit_hook(repo, state, {
        "FAKE_CLAUDE_RESULT": "APPROVED",
        "FAKE_CLAUDE_STDIN_FILE": str(prompt_dump),
    })

    prompt = prompt_dump.read_text(encoding="utf-8")
    # The reviewer has no shell and no git tool, so the patch has to be in the prompt.
    assert "diff --git" in prompt or "f.txt" in prompt


def test_the_session_is_reused_on_the_second_attempt(home, tmp_path):
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    argv_dump = tmp_path / "argv.jsonl"

    for attempt in range(2):
        if attempt == 1:
            # The first approval COMMITS and consumes the staging, so a second commit reviews a
            # fresh change -- which is exactly when the reviewer session must be resumed.
            (Path(repo) / "f.txt").write_text("three\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "f.txt"], check=True)
        run_commit_hook(repo, state, {
            "FAKE_CLAUDE_RESULT": "APPROVED",
            "FAKE_CLAUDE_ARGV_FILE": str(argv_dump),
        })

    calls = [json.loads(line) for line in argv_dump.read_text(encoding="utf-8").splitlines()]
    assert "--session-id" in calls[0]
    assert "--resume" in calls[1], "the second attempt must resume, not start a new reviewer"
    first_id = calls[0][calls[0].index("--session-id") + 1]
    assert calls[1][calls[1].index("--resume") + 1] == first_id


def test_a_rate_limited_review_blocks_with_an_actionable_message(home, tmp_path):
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)

    _process, output = run_commit_hook(repo, state, {
        "FAKE_CLAUDE_RATE_LIMITED": "1",
        "FAKE_CLAUDE_EXIT": "1",
    })

    reason = (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    assert (output.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny"
    assert "usage limit" in reason.lower()
    # It has to name a way out, or a fail-closed gate becomes an unexplained wall.
    assert "/reviewerModel" in reason or "backend" in reason.lower()


def test_selecting_claude_never_invokes_codex_or_pi(home, tmp_path):
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    tripwire = tmp_path / "other-backend-was-invoked"
    recorder = tmp_path / "recorder.py"
    recorder.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "open({!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.exit(97)\n".format(str(tripwire)),
        encoding="utf-8",
    )
    recorder.chmod(0o755)

    run_commit_hook(repo, state, {
        "FAKE_CLAUDE_RESULT": "APPROVED",
        "REVIEWER_CODEX_BIN": str(recorder),
        "REVIEWER_PI_BIN": str(recorder),
    })

    assert not tripwire.exists(), tripwire.read_text(encoding="utf-8") if tripwire.exists() else ""


def test_the_project_brief_is_appended_on_the_first_turn_only(home, tmp_path):
    # Safe mode disables CLAUDE.md, plugins and hooks, so the brief has nowhere else to travel
    # but --append-system-prompt. On a RESUMED turn the child already holds it, and re-sending
    # it would grow the system prompt on every commit for no gain.
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    argv_dump = tmp_path / "argv.jsonl"

    for attempt in range(2):
        if attempt == 1:
            # The first approval COMMITS and consumes the staging, so the second turn reviews a
            # fresh change on a RESUMED session, where the brief must not be re-sent.
            (Path(repo) / "f.txt").write_text("three\n", encoding="utf-8")
            subprocess.run(["git", "-C", repo, "add", "f.txt"], check=True)
        run_commit_hook(repo, state, {
            "FAKE_CLAUDE_RESULT": "APPROVED",
            "FAKE_CLAUDE_ARGV_FILE": str(argv_dump),
        })

    calls = [json.loads(line) for line in argv_dump.read_text(encoding="utf-8").splitlines()]
    assert "--append-system-prompt" in calls[0]
    brief = calls[0][calls[0].index("--append-system-prompt") + 1]
    assert "be strict" in brief, "the project's own reviewerAgent.md has to reach the reviewer"
    assert "--append-system-prompt" not in calls[1]


def test_a_patch_too_large_to_hand_over_blocks_instead_of_being_truncated(home, tmp_path):
    # The reviewer cannot fetch the diff itself, so a patch that does not fit is a patch it
    # cannot judge. Half a diff must never be able to produce an approval.
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    argv_dump = tmp_path / "argv.jsonl"

    _process, output = run_commit_hook(repo, state, {
        "FAKE_CLAUDE_RESULT": "APPROVED",
        "FAKE_CLAUDE_ARGV_FILE": str(argv_dump),
        "REVIEWER_STAGED_PATCH_MAX": "10",
    })

    hook_out = output.get("hookSpecificOutput") or {}
    assert hook_out.get("permissionDecision") == "deny"
    assert "full" in hook_out.get("permissionDecisionReason", "").lower()
    assert not argv_dump.exists(), "no reviewer may be asked to rule on a partial patch"


def test_the_stop_supervisor_runs_on_its_own_claude_session(home, tmp_path):
    # The stop role must never share the commit reviewer's session: supervision runs at every
    # turn end, and its framing bled into the next commit audit when the two were one session.
    state = tmp_path / "rh"
    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.set_stopcontinue_active(repo)
    env = dict(os.environ)
    env.update(
        REVIEWER_HOME=str(state),
        REVIEWER_STATE_DIR=str(state),
        REVIEWER_BACKEND="claude",
        REVIEWER_CLAUDE_BIN=str(Path(__file__).resolve().parent / "fake_claude.py"),
        FAKE_CLAUDE_RESULT="STOP_OK",
    )
    env.pop("REVIEWER_CONFIG_FILE", None)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "all done"}]}}) + "\n",
        encoding="utf-8",
    )
    payload = {"cwd": repo, "session_id": "claude-session", "stop_hook_active": False,
               "transcript_path": str(transcript)}
    subprocess.run(
        [sys.executable, str(ROOT / "stop_hook.py")], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )

    stop_sid = rl.read_claude_session(repo, "claude-session", role=rl.ROLE_STOP)
    reviewer_sid = rl.read_claude_session(repo, "claude-session", role=rl.ROLE_REVIEWER)
    assert stop_sid, "the stop supervisor must establish its own claude session"
    assert stop_sid != reviewer_sid


def test_a_usage_limit_lets_a_stop_proceed_but_says_why(home, tmp_path, monkeypatch, capsys):
    """Stop supervision is fail-OPEN, so an exhausted allowance must not wedge the agent -- and
    must not vanish either. Silently allowing every stop is indistinguishable from the feature
    being switched off, which is exactly what happened in the live matrix while the rate-limit
    detector was misfiring."""
    import stop_hook

    repo = make_repo(tmp_path)
    rl.set_active(repo)
    rl.set_stopcontinue_active(repo)
    monkeypatch.setenv("REVIEWER_BACKEND", "claude")
    monkeypatch.setattr(
        rl, "run_claude_review",
        lambda *_a, **_k: (1, "", "the Claude usage limit was reached, so the review could not "
                                  "run: Claude usage limit reached. Resets at 8pm."),
    )
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant",
                    "message": {"role": "assistant",
                                "content": [{"type": "text", "text": "shall I continue?"}]}}) +
        "\n", encoding="utf-8")

    code = stop_hook._main({
        "cwd": repo, "session_id": "claude-session", "stop_hook_active": False,
        "transcript_path": str(transcript),
    })

    out = capsys.readouterr().out
    assert code == 0, "stop supervision must fail open"
    assert '"decision": "block"' not in out, "a quota failure must not block the stop"
    assert "usage limit" in out.lower(), "the operator is told nothing about why"
