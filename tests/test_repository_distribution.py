# SPDX-License-Identifier: MPL-2.0
"""Packaging facts that a machine reads: manifests, hook wiring, command invocation.

This file used to also pin the wording of `README.md`, `SECURITY.md` and `PRIVACY.md` -- roughly
thirty tests asserting that particular sentences appeared, in a particular order. That was
removed deliberately: **documentation is prose, and prose should not need a passing test to be
edited.** The cost was real and one-sided -- every wording improvement broke a test that then had
to be rewritten to match, which taxes exactly the edits most worth making.

What survives is the part a person cannot verify by reading: machine-consumed structure whose
breakage is silent. A `hooks.json` that names the wrong interpreter, a marketplace entry that
resolves to nothing, or a slash command with a hardcoded `/usr/bin/python3` all fail at install
time on someone else's machine rather than in review.

The trade-off is stated because it has a consequence: nothing here now checks that the liability
notice stays above the installation instructions, or that the no-reliance banners survive. Those
are ordinary prose obligations, kept by reading rather than by CI.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_PLUGIN = ROOT / "plugins" / "reviewer-agent-codex"


def test_codex_marketplace_entry_resolves_the_bundled_plugin():
    marketplace = json.loads(
        (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entries = [
        entry for entry in marketplace["plugins"]
        if entry.get("name") == "reviewer-agent-codex"
    ]

    assert len(entries) == 1
    assert entries[0]["source"] == {
        "source": "local",
        "path": "./plugins/reviewer-agent-codex",
    }
    assert entries[0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert entries[0]["category"] == "Productivity"
    manifest = json.loads(
        (CODEX_PLUGIN / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == entries[0]["name"]
    assert manifest["mcpServers"] == "./.mcp.json"


def test_claude_hooks_resolve_python_from_path_in_exec_form():
    """Exec form with a bare `python3`, resolved from PATH.

    A hardcoded interpreter path is the classic install-time failure: it works on the machine it
    was written on and fails everywhere else. Exec form additionally keeps the script name out of
    a shell string -- see `_hook_timeout_ceiling`, which had to learn to read `args` because of
    exactly this shape."""
    hooks_path = ROOT / "hooks" / "hooks.json"
    payload = json.loads(hooks_path.read_text(encoding="utf-8"))

    expected_scripts = {
        "commit_hook.py",
        "askq_answer_hook.py",
        "session_start_hook.py",
        "stop_hook.py",
    }
    command_hooks = [
        hook
        for event_groups in payload["hooks"].values()
        for group in event_groups
        for hook in group["hooks"]
    ]

    assert {hook["args"][0].rsplit("/", 1)[-1] for hook in command_hooks} == expected_scripts
    for hook in command_hooks:
        assert hook["command"] == "python3"
        assert hook["args"] == [
            f"${{CLAUDE_PLUGIN_ROOT}}/{hook['args'][0].rsplit('/', 1)[-1]}"
        ]

    assert "/usr/bin/python3" not in hooks_path.read_text(encoding="utf-8")


def test_claude_commands_do_not_hardcode_the_python_install_path():
    """Slash commands are markdown, but this is not a prose check: the frontmatter and the `!`
    line are what Claude Code executes, so a hardcoded interpreter breaks the command."""
    command_paths = tuple(sorted((ROOT / "commands").glob("*.md")))

    for path in command_paths:
        text = path.read_text(encoding="utf-8")
        assert "/usr/bin/python3" not in text, path
        assert "Bash(python3:*)" in text, path
        assert "disable-model-invocation: true" in text, path
        assert "!`python3 " in text, path

    skill = (ROOT / "skills" / "reviewerAgent" / "SKILL.md").read_text(encoding="utf-8")
    assert "/usr/bin/python3" not in skill
    assert "Bash(python3:*)" in skill
    assert "!`python3 " in skill
