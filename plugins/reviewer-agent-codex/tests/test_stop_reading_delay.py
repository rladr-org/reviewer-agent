# SPDX-License-Identifier: MPL-2.0
import json
import threading
from pathlib import Path

import pytest

from reviewer_codex import backend, mcp_server, state, stop_tool, supervisor
from reviewer_codex.cancellation import Cancellation


def event(repo):
    return dict(cwd=str(repo), session_id="reading", turn_id="turn",
                hook_event_name="Stop", last_assistant_message="Done.")


@pytest.mark.parametrize("env,configured,expected", [
    (None, None, 60), (None, 30, 30), ("5", 30, 5), ("0", 30, 0),
    ("-1", None, 0), ("999", None, 300), ("invalid", 30, 30),
    (None, "invalid", 60), (None, True, 60), ("nan", None, 60),
])
def test_reading_delay_configuration(isolated_state, monkeypatch, env, configured, expected):
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raising=False)
    if env is not None:
        monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", env)
    isolated_state.mkdir(parents=True, exist_ok=True)
    state.config_file().write_text(json.dumps({"stopContinueDelaySecs": configured}))
    assert state.stopcontinue_delay_secs() == expected


def test_native_review_waits_before_starting_backend(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "15")
    seen = []
    monkeypatch.setattr(Cancellation, "wait", lambda self, seconds: seen.append(seconds) or False)

    def review(*args, **kwargs):
        assert seen == [15]
        seen.append("review")
        return 0, "Finish the task.", ""

    monkeypatch.setattr(backend, "review_turn", review)
    assert supervisor.supervise_hook(event(git_repo))["decision"] == "block"
    assert seen == [15, "review"]


def test_interrupt_during_reading_delay_never_starts_review(git_repo, isolated_state, monkeypatch):
    state.set_feature(git_repo, "stop")
    monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", "300")
    waiting = threading.Event()

    original_wait = Cancellation.wait

    def observed_wait(self, timeout=None):
        assert timeout == 300
        waiting.set()
        return original_wait(self, timeout)

    monkeypatch.setattr(Cancellation, "wait", observed_wait)
    token = Cancellation()
    calls = []
    monkeypatch.setattr(backend, "review_turn", lambda *a, **k: calls.append(True) or (0, "STOP_OK", ""))
    results = []
    worker = threading.Thread(target=lambda: results.append(mcp_server._native_stop(event(git_repo), token)))
    worker.start()
    try:
        assert waiting.wait(5), "review started without waiting for the reading window"
        payload = dict(event(git_repo), hook_event_name="Interrupt")
        assert mcp_server._interrupt_stop(payload) == {}
        worker.join(2)
        assert not worker.is_alive()
        assert results == [{}]
        assert calls == []
        assert not state.consume_stop_permit(git_repo, "reading")
        assert state.consume_stop_abort_bypass(git_repo, "reading", "next-turn")
    finally:
        token.cancel()
        worker.join(2)


def test_stop_timeout_includes_maximum_reading_delay():
    root = Path(__file__).resolve().parents[1]
    hooks = json.loads((root / "hooks/hooks.json").read_text())["hooks"]
    timeout = hooks["Stop"][0]["hooks"][0]["timeout"]
    assert timeout >= backend.STOP_REVIEW_TIMEOUT + 300 + 30
