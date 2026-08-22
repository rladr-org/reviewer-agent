# SPDX-License-Identifier: MPL-2.0
"""Plugin packaging: the writable STATE dir must be separable from the CODE dir.

In a plugin install the code lives under ${CLAUDE_PLUGIN_ROOT} (managed by the plugin
system and overwritten on update), so flags / per-session reviewer ids / logs / reviews
must NOT be written there. REVIEWER_STATE_DIR selects the writable location; the legacy
REVIEWER_HOME still works for the pre-plugin single-dir install.
"""
from pathlib import Path

import reviewer_lib as rl

PROJ = "/Users/x/proj"


def test_state_dir_env_routes_all_state(monkeypatch, tmp_path):
    d = tmp_path / "st"
    monkeypatch.setenv("REVIEWER_STATE_DIR", str(d))
    monkeypatch.delenv("REVIEWER_HOME", raising=False)

    assert rl.reviewer_state_home() == d
    assert str(rl.flag_file("/proj")).startswith(str(d))
    assert str(rl.stopcontinue_flag_file("/proj")).startswith(str(d))
    assert str(rl.session_id_file(PROJ, "sid")).startswith(str(d))
    assert str(rl.nudge_count_file(PROJ, "sid")).startswith(str(d))

    rl.log("hello state")
    assert (d / "logs" / "reviewer.log").exists()

    p = rl.save_review("some review text", proj="/proj", claude_session_id="sid")
    assert str(p).startswith(str(d))
    assert str(rl.review_history_dir("/proj")).startswith(str(d))


def test_reviewer_home_env_is_backward_compatible(monkeypatch, tmp_path):
    d = tmp_path / "legacy"
    monkeypatch.delenv("REVIEWER_STATE_DIR", raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(d))

    assert rl.reviewer_state_home() == d
    assert str(rl.flag_file("/proj")).startswith(str(d))


def test_default_state_home_is_outside_the_code_dir(monkeypatch):
    monkeypatch.delenv("REVIEWER_STATE_DIR", raising=False)
    monkeypatch.delenv("REVIEWER_HOME", raising=False)

    home = rl.reviewer_state_home().resolve()
    assert home == (Path.home() / ".claude" / "reviewer-agent").resolve()

    # The default state dir must never sit inside the code/plugin dir (which is wiped on update).
    code_dir = Path(rl.__file__).resolve().parent
    assert home != code_dir
    assert code_dir not in home.parents
