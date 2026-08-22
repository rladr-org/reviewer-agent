# SPDX-License-Identifier: MPL-2.0
"""Gate-safety signals the pi backend derives from pi's own event stream.

`parse_pi_tool_flags` decides whether an APPROVED verdict may stand. It had no tests at all,
which is how it drifted from its own docstring: the docstring says a `git_read` error, the code
matched ANY tool's error.

Every fixture here is a real event shape, captured 2026-08-02 from `pi --mode json` running the
reviewer's own `pi_argv`, not an invented one:

    {"type":"tool_execution_start","toolCallId":"call_x","toolName":"git_read",
     "args":{"subcommand":"diff","staged":true}}
    {"type":"tool_execution_end","toolCallId":"call_x","toolName":"git_read","isError":false}
"""
import json

import reviewer_lib as rl


def _events(*evs):
    return "\n".join(json.dumps(e) for e in evs) + "\n"


def _start(call_id, tool, **args):
    return {"type": "tool_execution_start", "toolCallId": call_id,
            "toolName": tool, "args": args}


def _end(call_id, tool, is_error=False, truncated=None):
    ev = {"type": "tool_execution_end", "toolCallId": call_id,
          "toolName": tool, "isError": is_error}
    if truncated is not None:
        ev["result"] = {"details": {"truncated": truncated}}
    return ev


STAGED_OK = _events(
    _start("c1", "git_read", subcommand="diff", staged=True),
    _end("c1", "git_read"),
)


def test_a_healthy_staged_read_sets_the_seen_flag_and_no_alarms():
    f = rl.parse_pi_tool_flags(STAGED_OK)
    assert f["saw_staged_read"] is True
    assert f["tool_error"] is False
    assert f["truncated"] is False


def test_no_git_read_at_all_is_the_missing_extension_case():
    """The failure this flag exists for. `-t` is an allowlist of NAMES: with the extension
    absent, `git_read` enables nothing, the model never calls it, and NO error is raised.
    Silence is the whole signal -- there is nothing else to detect it by."""
    f = rl.parse_pi_tool_flags(_events(
        _start("c1", "read", path="calc.py"),
        _end("c1", "read"),
    ))
    assert f["saw_staged_read"] is False
    assert f["tool_error"] is False   # nothing errored; that is exactly the danger


def test_an_unstaged_git_read_does_not_count_as_seeing_the_staged_patch():
    # `git_read status` proves the extension is installed, not that the reviewer read the diff.
    f = rl.parse_pi_tool_flags(_events(
        _start("c1", "git_read", subcommand="status"),
        _end("c1", "git_read"),
    ))
    assert f["saw_staged_read"] is False


def test_a_staged_read_that_errored_is_not_a_read():
    f = rl.parse_pi_tool_flags(_events(
        _start("c1", "git_read", subcommand="diff", staged=True),
        _end("c1", "git_read", is_error=True),
    ))
    assert f["saw_staged_read"] is False
    assert f["tool_error"] is True


def test_an_unrelated_tool_error_is_not_a_git_read_failure():
    """REGRESSION, reproduced live on 2026-08-02. The operator's pi-permission-system config
    allows read/write/edit/git_read and falls back to "*": "ask"; in `--mode json` there is no
    UI, so a `grep` call returns isError=true. It does not hang -- it denies fast.

    The parser set `tool_error` on ANY tool's error, so that denied grep overrode a good
    approval with "A git_read call ERRORED", blocking the commit and naming a cause that never
    happened. The staged patch had been read successfully in the same run."""
    f = rl.parse_pi_tool_flags(_events(
        _start("c1", "git_read", subcommand="diff", staged=True),
        _end("c1", "git_read"),
        _start("c2", "grep", pattern="TODO"),
        _end("c2", "grep", is_error=True),
    ))
    assert f["tool_error"] is False
    assert f["saw_staged_read"] is True


def test_truncation_is_still_caught_on_the_git_read_result():
    f = rl.parse_pi_tool_flags(_events(
        _start("c1", "git_read", subcommand="diff", staged=True),
        _end("c1", "git_read", truncated=True),
    ))
    assert f["truncated"] is True


def test_an_end_event_without_its_start_is_not_credited_as_a_staged_read():
    """Pairing is by toolCallId. Crediting a bare end event would let any git_read call --
    or a stream that lost its start event -- satisfy the guard."""
    f = rl.parse_pi_tool_flags(_events(_end("c1", "git_read")))
    assert f["saw_staged_read"] is False


def test_a_garbled_stream_reads_as_nothing_seen():
    # Fail closed: an unparseable stream has not established that anything was read.
    for stream in ("", "not json\n", "{}\n", "null\n"):
        assert rl.parse_pi_tool_flags(stream)["saw_staged_read"] is False


def test_an_approval_is_overridden_when_the_staged_patch_was_never_read(tmp_path, monkeypatch):
    """The gate half of the same defect. A verdict is only as good as what the reviewer saw,
    and with the extension absent it saw the WORKING TREE through `read` -- which the prompt
    forbids it from citing as staged content, and which can differ from the staged set
    entirely. Approving on that is a silent blind approval, the one failure mode worse than
    over-blocking on a fail-closed gate."""
    import reviewer_backends

    repo = tmp_path / "r"
    repo.mkdir()
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setattr(reviewer_backends, "pi_argv", lambda *a, **k: ["/bin/echo", "x"])
    monkeypatch.setattr(reviewer_backends, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(reviewer_backends, "parse_pi_final_text", lambda out: "APPROVED")
    monkeypatch.setattr(
        reviewer_backends, "parse_pi_tool_flags",
        lambda out: {"truncated": False, "tool_error": False, "saw_staged_read": False})

    _, final, _ = rl.run_pi_review(str(repo), "claude-x", "PROMPT")
    assert not rl.is_approved(final)
    assert "git_read" in final


def test_non_commit_pi_turn_does_not_require_a_staged_diff_read(tmp_path, monkeypatch):
    """AskUserQuestion and stopContinue do not audit a staged set. Their Pi verdicts must not
    be replaced by the commit gate's missing-git_read rejection."""
    import reviewer_backends

    repo = tmp_path / "r-non-commit"
    repo.mkdir()
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setattr(reviewer_backends, "pi_argv", lambda *a, **k: ["/bin/echo", "x"])
    monkeypatch.setattr(reviewer_backends, "_run", lambda *a, **k: (0, "", ""))
    monkeypatch.setattr(
        reviewer_backends, "parse_pi_final_text", lambda out: "Continue with the task."
    )
    monkeypatch.setattr(
        reviewer_backends,
        "parse_pi_tool_flags",
        lambda out: {"truncated": False, "tool_error": False, "saw_staged_read": False},
    )

    _, final, _ = rl.run_pi_review(
        str(repo),
        "claude-x",
        "PROMPT",
        require_staged_read=False,
        role=rl.ROLE_STOP,
    )

    assert final == "Continue with the task."
