# SPDX-License-Identifier: MPL-2.0
"""Native Stop reviews must not wait for an unobservable GUI reading window."""

import json
from pathlib import Path

import pytest

from reviewer_codex import backend, state, supervisor
from reviewer_codex.cancellation import Cancellation


@pytest.mark.parametrize("env,configured", [(None, None), ("300", None), (None, 300)])
@pytest.mark.parametrize("verdict", ["STOP_OK", "Finish the authorized task."])
def test_native_stop_reviews_immediately_despite_legacy_delay_settings(
    git_repo, isolated_state, monkeypatch, env, configured, verdict
):
    monkeypatch.delenv("REVIEWER_STOPCONTINUE_DELAY_SECS", raising=False)
    if env is not None:
        monkeypatch.setenv("REVIEWER_STOPCONTINUE_DELAY_SECS", env)
    isolated_state.mkdir()
    config = {"model": "keep-this", "other": {"x": 1}}
    if configured is not None:
        config["stopContinueDelaySecs"] = configured
    state.config_file().write_text(json.dumps(config))
    state.set_feature(git_repo, "stop")
    seen = []
    # Record a wait without actually delaying the red run by up to five minutes.
    monkeypatch.setattr(Cancellation, "wait", lambda self, seconds: seen.append(seconds) or False)

    def review(*args, **kwargs):
        seen.append("review")
        return 0, verdict, ""

    monkeypatch.setattr(backend, "review_turn", review)
    result = supervisor.supervise_hook(dict(
        cwd=str(git_repo), session_id="immediate", turn_id="turn",
        hook_event_name="Stop", last_assistant_message="Done.",
    ), cancel_event=Cancellation())

    assert seen == ["review"]
    if verdict == "STOP_OK":
        assert result == {}
    else:
        assert result["decision"] == "block"
    assert state.load_config() == config


def test_native_stop_timeout_covers_review_without_a_reading_window():
    root = Path(__file__).resolve().parents[1]
    hook = json.loads((root / "hooks/hooks.json").read_text())["hooks"]["Stop"][0]["hooks"][0]
    assert backend.STOP_REVIEW_TIMEOUT + 30 <= hook["timeout"] <= 360
