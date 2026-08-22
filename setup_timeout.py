#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Set `askUserQuestionTimeout` in the USER settings file.

Why this exists as a script: the question-answering hook only ever fires when Claude Code
auto-closes an unanswered AskUserQuestion, and that only happens when
`askUserQuestionTimeout` is set. The setting is read from USER settings
(~/.claude/settings.json) ONLY -- not project or project-local settings -- and a plugin
manifest cannot ship it (`settings` in plugin.json supports only `agent` and
`subagentStatusLine`). So it has to be merged into the user's own file.

Usage:
  setup_timeout.py            # set 60s, but never replace a value the user already chose
  setup_timeout.py 5m         # set explicitly (overrides an existing value)

Values are the ones Claude Code accepts: 60s, 5m, 10m, never. The merge is conservative --
every other key is preserved, the previous file is backed up to <settings>.bak, and a
malformed settings.json is reported rather than overwritten.
"""
import json
import os
import sys
from pathlib import Path

VALID = ("60s", "5m", "10m", "never")
DEFAULT = "60s"
KEY = "askUserQuestionTimeout"


def settings_file():
    """REVIEWER_SETTINGS_FILE lets the tests (and anyone with a non-standard layout) point
    this at another file; otherwise the real user settings."""
    env = os.environ.get("REVIEWER_SETTINGS_FILE")
    return Path(env) if env else Path.home() / ".claude" / "settings.json"


def load(path):
    """Return (data, error). A missing file is an empty config; a CORRUPT file is an error,
    never an empty config -- treating a syntax error as {} would silently delete every
    setting the user has."""
    if not path.exists():
        return {}, None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, "could not read {}: {}".format(path, e)
    if not text.strip():
        return {}, None
    try:
        data = json.loads(text)
    except ValueError as e:
        return None, "{} is not valid JSON ({}). Fix it by hand first.".format(path, e)
    if not isinstance(data, dict):
        return None, "{} does not contain a JSON object.".format(path)
    return data, None


def ensure(value=None):
    """Establish the timeout; return (exit_code, detail) without printing.

    Split out of main() so `/reviewerAgent on` can arm the setting IN-PROCESS and fold the
    outcome into its own output: the hook that answers unanswered questions never fires
    unless this key exists, so enabling the reviewer has to establish it."""
    requested = (value or "").strip()
    explicit = bool(requested)
    value = requested or DEFAULT
    if value not in VALID:
        return 2, "ERROR - invalid value {!r}. Use one of: {}.".format(value, ", ".join(VALID))

    path = settings_file()
    data, err = load(path)
    if err:
        return 1, "ERROR - " + err

    current = data.get(KEY)
    if current == value:
        return 0, "already {} in {}".format(value, path)
    # A bare run must not silently downgrade a longer window the operator chose on purpose.
    if current and not explicit:
        return 0, "left as {} in {} (pass a value to change it: {})".format(
            current, path, ", ".join(VALID)
        )

    if path.exists():
        try:
            backup = Path(str(path) + ".bak")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as e:
            return 1, "ERROR - could not write backup: {}".format(e)

    data[KEY] = value
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        return 1, "ERROR - could not write {}: {}".format(path, e)

    was = " (was {})".format(current) if current else ""
    return 0, "{} set to {}{} - takes effect in NEW sessions.".format(path, value, was)


def main(argv):
    code, detail = ensure(argv[1] if len(argv) > 1 else None)
    print("reviewerTimeout: " + detail, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
