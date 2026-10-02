# SPDX-License-Identifier: MPL-2.0
"""An operator who presses Enter while a stop review is running gets the turn.

A message submitted while the agent's turn is still running is queued, and a Stop hook's
supervisor review is part of that turn. The supervisor's directive used to win anyway, so the
operator had to press Escape as well (2026-09-02: "no continue" was queued 4s after the agent's
reply, and the push still landed 39s later). Claude Code records the submit in the transcript as
a `queue-operation` `enqueue` event, so the hook watches for one and gives way: the review is
skipped, killed while it runs, or its verdict dropped before it is enacted.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import reviewer_backends
import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
STOP = str(ROOT / "stop_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")

_TICK = "stopContinueWatchdog tick -- Check these three paths INDIVIDUALLY"
_NOTICE = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
           "</task-notification>")
_DIRECTIVE = "Continue: run the remaining tests."


def _assistant(text, model="claude-opus-5-5"):
    return {"type": "assistant", "message": {"role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}]}}


def _enqueue(text):
    # The shape Claude Code writes when the operator submits while a turn is running.
    return {"type": "queue-operation", "operation": "enqueue",
            "timestamp": "2026-10-02T23:00:00.000Z", "sessionId": "claude-stop",
            "content": text}


def _write(tmp_path, events):
    p = tmp_path / "transcript.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    return str(p)


def _append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text)


def _alive(pid):
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _wait_gone(pid, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.02)
    return not _alive(pid)


# --------------------------------------------------------------------------
# The watch: which queued entries are the operator's reply
# --------------------------------------------------------------------------
def test_a_reply_queued_after_the_agents_final_message_is_seen(tmp_path):
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?"), _enqueue("yes, A")])
    assert rl.operator_reply_watch(tr).replied() is True


def test_a_message_absorbed_before_the_final_message_is_not_a_reply(tmp_path):
    tr = _write(tmp_path, [_enqueue("also check the docs"), _assistant("Done; docs checked.")])
    assert rl.operator_reply_watch(tr).replied() is False


@pytest.mark.parametrize("text", [_TICK, _NOTICE], ids=["watchdog-tick", "task-notification"])
def test_host_injected_queue_entries_are_not_a_reply(tmp_path, text):
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?"), _enqueue(text)])
    assert rl.operator_reply_watch(tr).replied() is False


def test_a_reply_written_after_the_watch_started_is_seen_once_complete(tmp_path):
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?")])
    watch = rl.operator_reply_watch(tr)
    assert watch.replied() is False
    line = json.dumps(_enqueue("no, stop there"))
    _append(tr, line[:25])  # caught mid-write
    assert watch.replied() is False
    _append(tr, line[25:] + "\n")
    assert watch.replied() is True


def test_a_missing_transcript_never_counts_as_a_reply(tmp_path):
    assert rl.operator_reply_watch(str(tmp_path / "missing.jsonl")).replied() is False
    assert rl.operator_reply_watch(None).replied() is False


# --------------------------------------------------------------------------
# The reviewer call can be cancelled by its caller, for every backend
# --------------------------------------------------------------------------
def test_a_review_is_torn_down_when_its_caller_cancels_it(tmp_path):
    pid_file = tmp_path / "reviewer.pid"
    # Published by rename, so the file exists only once the pid is in it.
    script = ("import os, time; from pathlib import Path; "
              "Path({0!r} + '.tmp').write_text(str(os.getpid())); "
              "os.replace({0!r} + '.tmp', {0!r}); time.sleep(60)").format(str(pid_file))
    polls = []

    def should_cancel():
        polls.append(1)
        return pid_file.exists()

    started = time.monotonic()
    code, _out, err = reviewer_backends._run([sys.executable, "-c", script], timeout=60,
                                             should_cancel=should_cancel)
    assert time.monotonic() - started < 15
    assert code == reviewer_backends.CANCELLED_EXIT, err
    assert polls
    assert _wait_gone(int(pid_file.read_text()))


def test_pi_and_claude_reviews_hand_the_cancel_check_to_the_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    seen = []

    def fake_run(argv, **kwargs):
        seen.append(kwargs.get("should_cancel"))
        return 1, "", "stubbed"

    monkeypatch.setattr(reviewer_backends, "_run", fake_run)

    def cancel():
        return False

    reviewer_backends.run_pi_review(str(tmp_path), "claude-stop", "prompt", should_cancel=cancel)
    reviewer_backends.run_claude_review(str(tmp_path), "claude-stop", "prompt",
                                        should_cancel=cancel)
    assert seen == [cancel, cancel]


def test_the_pre_enactment_delay_ends_when_the_caller_cancels(monkeypatch):
    import reviewer_stopcontinue as sc

    slept = []
    monkeypatch.setattr(sc, "_delay_sleep", lambda n: slept.append(n))
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "30")
    checks = []

    def should_cancel():
        checks.append(1)
        return len(checks) >= 3

    assert sc.apply_stopcontinue_delay("continue", should_cancel=should_cancel) is True
    assert sum(slept) < 30


# --------------------------------------------------------------------------
# The Stop hook gives way
# --------------------------------------------------------------------------
def _setup(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    repo = str(repo)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    return home, repo


def _payload(repo, tr):
    return {"cwd": repo, "session_id": "claude-stop", "transcript_path": tr,
            "hook_event_name": "Stop", "stop_hook_active": False}


def _run_hook(repo, tr, home, **extra):
    env = dict(os.environ)
    env.update({"REVIEWER_HOME": home, "REVIEWER_CODEX_BIN": FAKE, "FAKE_CODEX_EXIT": "0",
                "FAKE_CODEX_STDOUT": _DIRECTIVE})
    env.update(extra)
    return subprocess.run([sys.executable, STOP], input=json.dumps(_payload(repo, tr)),
                          capture_output=True, text=True, env=env)


def _output(p):
    return json.loads(p.stdout) if p.stdout.strip() else {}


def _log_text(home):
    p = Path(home) / "logs" / "reviewer.log"
    return p.read_text() if p.exists() else ""


def test_a_reply_queued_before_the_review_skips_the_supervisor(tmp_path, monkeypatch):
    home, repo = _setup(tmp_path, monkeypatch)
    argv_file = tmp_path / "argv.jsonl"
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?"), _enqueue("yes, A")])
    p = _run_hook(repo, tr, home, FAKE_CODEX_ARGV_FILE=str(argv_file))
    assert p.returncode == 0, p.stderr
    assert _output(p).get("decision") != "block"
    assert not argv_file.exists()  # the supervisor never ran
    assert "operator replied" in _log_text(home)


def test_pressing_enter_during_the_review_kills_it_and_allows_the_stop(tmp_path, monkeypatch):
    home, repo = _setup(tmp_path, monkeypatch)
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?")])
    started = time.monotonic()
    p = _run_hook(repo, tr, home, FAKE_CODEX_SLEEP="60", FAKE_CODEX_APPEND_FILE=tr,
                  FAKE_CODEX_APPEND_LINE=json.dumps(_enqueue("yes, A")))
    assert time.monotonic() - started < 20  # not the reviewer's 60s
    assert p.returncode == 0, p.stderr
    assert _output(p).get("decision") != "block"
    log = _log_text(home)
    assert "operator replied" in log and "cancelled" in log


def test_a_reply_queued_as_the_verdict_arrives_still_wins(tmp_path, monkeypatch):
    home, repo = _setup(tmp_path, monkeypatch)
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?")])
    p = _run_hook(repo, tr, home, FAKE_CODEX_APPEND_FILE=tr,
                  FAKE_CODEX_APPEND_LINE=json.dumps(_enqueue("no")))
    assert p.returncode == 0, p.stderr
    assert _output(p).get("decision") != "block"
    assert "operator replied" in _log_text(home)


@pytest.mark.parametrize("text", [_TICK, _NOTICE], ids=["watchdog-tick", "task-notification"])
def test_a_host_injected_queue_entry_does_not_stop_the_directive(tmp_path, monkeypatch, text):
    home, repo = _setup(tmp_path, monkeypatch)
    tr = _write(tmp_path, [_assistant("This is a reasonable stopping point.")])
    p = _run_hook(repo, tr, home, FAKE_CODEX_APPEND_FILE=tr,
                  FAKE_CODEX_APPEND_LINE=json.dumps(_enqueue(text)))
    assert p.returncode == 0, p.stderr
    out = _output(p)
    assert out.get("decision") == "block"
    assert "run the remaining tests" in out["reason"]


def test_a_message_queued_before_the_final_reply_does_not_stop_the_directive(
        tmp_path, monkeypatch):
    home, repo = _setup(tmp_path, monkeypatch)
    tr = _write(tmp_path, [_enqueue("also check the docs"),
                           _assistant("Docs checked; this is a reasonable stopping point.")])
    p = _run_hook(repo, tr, home)
    assert p.returncode == 0, p.stderr
    assert _output(p).get("decision") == "block"


def _in_process(tmp_path, monkeypatch, fake_pi):
    import stop_hook as h

    home, repo = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    tr = _write(tmp_path, [_assistant("Shall I go ahead with A?")])
    monkeypatch.setattr(h.rl, "run_pi_review", lambda *a, **k: fake_pi(tr, k))
    return h, repo, tr, home


def test_a_dropped_directive_is_shown_to_the_operator_not_sent_to_the_agent(
        tmp_path, monkeypatch, capsys):
    calls = []

    def fake_pi(tr, kwargs):
        calls.append(kwargs)
        _append(tr, json.dumps(_enqueue("no")) + "\n")  # Enter, just as the verdict lands
        return 0, _DIRECTIVE, ""

    h, repo, tr, home = _in_process(tmp_path, monkeypatch, fake_pi)
    assert h._main(_payload(repo, tr)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("decision") != "block"
    assert "run the remaining tests" in out["systemMessage"]
    assert callable(calls[0].get("should_cancel"))
    # Not an approval: nothing was judged complete.
    assert rl.read_nudge_count(repo, "claude-stop") == 0


def test_enter_during_the_pre_enactment_delay_drops_the_verdict(tmp_path, monkeypatch, capsys):
    import reviewer_stopcontinue as sc

    h, repo, tr, home = _in_process(tmp_path, monkeypatch, lambda tr, k: (0, _DIRECTIVE, ""))
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "30")
    slept = []

    def sleep(n):
        if not slept:
            _append(tr, json.dumps(_enqueue("no")) + "\n")
        slept.append(n)

    monkeypatch.setattr(sc, "_delay_sleep", sleep)
    assert h._main(_payload(repo, tr)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("decision") != "block"
    assert sum(slept) < 30
