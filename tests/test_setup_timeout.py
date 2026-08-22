# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
import sys
from pathlib import Path

# The plugin keeps the python in scripts/; the legacy single-directory install keeps it at
# the root. Resolve either so this file is valid in both layouts.
_BASE = Path(__file__).resolve().parent.parent
_CANDIDATES = (_BASE / "scripts" / "setup_timeout.py", _BASE / "setup_timeout.py")
SETUP = str(next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0]))


def run_setup(settings_path, args=(), env_extra=None):
    env = dict(os.environ)
    env["REVIEWER_SETTINGS_FILE"] = str(settings_path)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, SETUP] + list(args), capture_output=True, text=True, env=env
    )


def write(settings_path, obj):
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def read(settings_path):
    return json.loads(settings_path.read_text(encoding="utf-8"))


def test_adds_the_setting_and_preserves_everything_else(tmp_path):
    # The whole risk of touching a user's settings.json is clobbering it. Unrelated keys,
    # including nested ones, must survive byte-for-byte in value.
    s = tmp_path / "settings.json"
    write(s, {"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command"}]}]}})
    p = run_setup(s)
    assert p.returncode == 0, p.stderr
    data = read(s)
    assert data["askUserQuestionTimeout"] == "60s"
    assert data["model"] == "opus"
    assert data["hooks"]["Stop"][0]["hooks"][0]["type"] == "command"


def test_creates_the_file_when_missing(tmp_path):
    s = tmp_path / "nested" / "settings.json"
    p = run_setup(s)
    assert p.returncode == 0, p.stderr
    assert read(s)["askUserQuestionTimeout"] == "60s"


def test_explicit_value_is_applied(tmp_path):
    s = tmp_path / "settings.json"
    write(s, {})
    p = run_setup(s, ["5m"])
    assert p.returncode == 0, p.stderr
    assert read(s)["askUserQuestionTimeout"] == "5m"


def test_no_arg_does_not_downgrade_a_deliberate_existing_value(tmp_path):
    # Re-running the default setup must not silently replace a value the user chose.
    s = tmp_path / "settings.json"
    write(s, {"askUserQuestionTimeout": "10m"})
    p = run_setup(s)
    assert p.returncode == 0, p.stderr
    assert read(s)["askUserQuestionTimeout"] == "10m"
    assert "10m" in p.stdout


def test_explicit_value_overrides_an_existing_value(tmp_path):
    s = tmp_path / "settings.json"
    write(s, {"askUserQuestionTimeout": "10m"})
    p = run_setup(s, ["60s"])
    assert p.returncode == 0, p.stderr
    assert read(s)["askUserQuestionTimeout"] == "60s"


def test_invalid_value_is_rejected_and_file_untouched(tmp_path):
    # Only the documented values work; anything else would silently disable the timeout.
    s = tmp_path / "settings.json"
    write(s, {"model": "opus"})
    before = s.read_text(encoding="utf-8")
    p = run_setup(s, ["30s"])
    assert p.returncode != 0
    assert "ERROR" in (p.stdout + p.stderr)
    assert s.read_text(encoding="utf-8") == before


def test_malformed_settings_is_not_destroyed(tmp_path):
    # A hand-edited settings.json with a syntax error must be reported, never overwritten.
    s = tmp_path / "settings.json"
    s.write_text('{"model": "opus",}', encoding="utf-8")
    p = run_setup(s)
    assert p.returncode != 0
    assert "ERROR" in (p.stdout + p.stderr)
    assert s.read_text(encoding="utf-8") == '{"model": "opus",}'


def test_writes_a_backup_before_modifying(tmp_path):
    s = tmp_path / "settings.json"
    write(s, {"model": "opus"})
    p = run_setup(s)
    assert p.returncode == 0, p.stderr
    backup = Path(str(s) + ".bak")
    assert backup.exists(), "no backup written"
    assert read(backup) == {"model": "opus"}  # backup holds the PRE-change content


def test_is_idempotent(tmp_path):
    s = tmp_path / "settings.json"
    write(s, {"model": "opus"})
    run_setup(s)
    first = read(s)
    p = run_setup(s)
    assert p.returncode == 0, p.stderr
    assert read(s) == first
