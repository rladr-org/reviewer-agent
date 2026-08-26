# SPDX-License-Identifier: MPL-2.0
"""`/stopContinueWatchdog on [interval]|off|status` toggle.

The watchdog REQUIRES stopContinue active (its tick relies on the Stop hook, inert otherwise),
so `on` errors until stopContinue is enabled. The flag file's content is the interval; `on N`
after `on M` re-writes it. `status` prints ACTIVE/INACTIVE for the tick's self-cancel check.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import reviewer_lib as rl

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
TOGGLE = str(ROOT / "stopcontinue_watchdog_toggle.py")
STOPCONTINUE_TOGGLE = str(ROOT / "stopcontinue_toggle.py")
SESSION_START = str(ROOT / "session_start_hook.py")


def run(action, proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    args = [action] if isinstance(action, str) else list(action)
    return subprocess.run(
        [sys.executable, TOGGLE, *args], capture_output=True, text=True, env=env
    )


def make_repo(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", str(proj)], check=True)
    return str(proj)


def enable_stopcontinue(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    p = subprocess.run(
        [sys.executable, STOPCONTINUE_TOGGLE, "on", "acceptRisk"],
        capture_output=True, text=True, env=env,
    )
    assert p.returncode == 0, p.stderr


def disable_stopcontinue(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    p = subprocess.run(
        [sys.executable, STOPCONTINUE_TOGGLE, "off"],
        capture_output=True, text=True, env=env,
    )
    assert p.returncode == 0, p.stderr


def wd_active(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.is_stopcontinue_watchdog_active(proj)


def wd_interval(home, proj, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", home)
    return rl.watchdog_interval(proj)


def test_on_requires_stopcontinue_active(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    p = run("on", proj, home)
    assert p.returncode != 0
    assert not wd_active(home, proj, monkeypatch)
    assert "stopContinue is not active" in (p.stdout + p.stderr)
    assert "/stopContinue on acceptRisk" in (p.stdout + p.stderr)


def test_on_sets_flag_with_default_interval_when_stopcontinue_active(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    p = run("on", proj, home)
    assert p.returncode == 0, p.stderr
    assert wd_active(home, proj, monkeypatch)
    assert wd_interval(home, proj, monkeypatch) == rl.WATCHDOG_DEFAULT_INTERVAL
    assert "ON" in p.stdout


def test_on_with_interval_sets_it(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    p = run(["on", "5"], proj, home)
    assert p.returncode == 0, p.stderr
    assert wd_interval(home, proj, monkeypatch) == 5


def test_interval_change_rewrites_the_stored_interval(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "3"], proj, home)
    assert wd_interval(home, proj, monkeypatch) == 3
    run(["on", "5"], proj, home)
    assert wd_interval(home, proj, monkeypatch) == 5


def test_on_rejects_out_of_range_and_nonint_intervals(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    for bad in ("0", "60", "90", "abc"):
        p = run(["on", bad], proj, home)
        assert p.returncode != 0, "interval {!r} should be rejected".format(bad)
        assert not wd_active(home, proj, monkeypatch)
        assert "interval must be an integer" in (p.stdout + p.stderr)


def test_off_clears_flag(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    assert wd_active(home, proj, monkeypatch)
    p = run("off", proj, home)
    assert p.returncode == 0
    assert not wd_active(home, proj, monkeypatch)
    assert "OFF" in p.stdout


def test_status_prints_active_inactive(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    assert run("status", proj, home).stdout.strip() == "INACTIVE"
    run("on", proj, home)
    assert run("status", proj, home).stdout.strip() == "ACTIVE"


def test_on_fails_when_not_a_git_repo(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = tmp_path / "plain"
    proj.mkdir()
    p = run("on", str(proj), home)
    assert p.returncode != 0
    assert "ERROR" in (p.stdout + p.stderr)


# --------------------------------------------------------------------------
# The `on`/`off` output carries the CronList-reconcile instruction the model acts on.
# --------------------------------------------------------------------------
def test_on_output_carries_the_arm_instruction(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    out = run(["on", "3"], proj, home).stdout
    assert "[stopContinueWatchdog]" in out
    assert rl.WATCHDOG_MARKER in out
    assert "*/3 * * * *" in out          # the target cron schedule for the interval
    assert "CronList" in out and "CronCreate" in out and "CronDelete" in out
    assert "stopContinueWatchdog tick" in out  # the tick prompt is included verbatim


def test_off_output_carries_the_cancel_instruction(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    out = run("off", proj, home).stdout
    assert rl.WATCHDOG_MARKER in out
    assert "CronDelete" in out


# --------------------------------------------------------------------------
# SessionStart backstop: re-arm context is injected iff the flag is on.
# --------------------------------------------------------------------------
def run_session_start(proj, home):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = home
    env["CLAUDE_PROJECT_DIR"] = proj
    return subprocess.run(
        [sys.executable, SESSION_START],
        input=json.dumps({"cwd": proj}),
        capture_output=True, text=True, env=env,
    )


def test_sessionstart_injects_rearm_context_when_flag_on(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "5"], proj, home)
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert rl.WATCHDOG_MARKER in ctx
    assert "*/5 * * * *" in ctx  # re-arms at the STORED interval


def test_sessionstart_injects_nothing_when_flag_off(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)  # stopContinue on, but watchdog OFF
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""  # no additionalContext emitted


# --------------------------------------------------------------------------
# Regression: `/stopContinue off` clears only the stopContinue flag, leaving the watchdog flag
# set. Because the tick relies on the (now inert) Stop hook, the watchdog must go dormant:
# `status` reports INACTIVE so the tick self-cancels, and SessionStart stops re-arming.
# --------------------------------------------------------------------------
def test_status_inactive_when_stopcontinue_turned_off_after_arming(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run("on", proj, home)
    assert run("status", proj, home).stdout.strip() == "ACTIVE"
    disable_stopcontinue(proj, home)
    assert wd_active(home, proj, monkeypatch)  # the watchdog flag itself is still set...
    assert run("status", proj, home).stdout.strip() == "INACTIVE"  # ...but it is not effective


def test_sessionstart_injects_nothing_when_stopcontinue_off_even_if_flag_on(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    run(["on", "5"], proj, home)
    disable_stopcontinue(proj, home)
    p = run_session_start(proj, home)
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == ""  # no re-arm context despite the flag being set
