# SPDX-License-Identifier: MPL-2.0
import json
import os
import subprocess
from pathlib import Path

from reviewer_codex import backend


ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_hook_contract():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert manifest["name"] == "reviewer-agent-codex"
    assert manifest["mcpServers"] == "./.mcp.json"
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp["mcpServers"]["reviewer-agent"]
    assert server["command"] == "python3"
    assert server["args"] == ["scripts/mcp_server.py"]
    assert server["cwd"] == "."
    assert server["tool_timeout_sec"] > backend.MAX_COMMIT_REVIEW_TIMEOUT
    assert set(("UserPromptSubmit", "PreToolUse", "PostToolUse", "SubagentStart", "SubagentStop", "Stop", "SessionEnd")) <= set(hooks)
    assert "SessionStart" not in hooks
    assert "hooks" not in manifest
    pre_tool_matchers = {group["matcher"] for group in hooks["PreToolUse"]}
    assert pre_tool_matchers == {"^Bash$", "^update_plan$"}


def test_no_claude_transcript_dependency():
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "reviewer_codex").glob("*.py")
    )
    assert ".claude" not in source
    assert "transcript_path" not in source


def test_nested_reviews_run_outside_short_hook_timeouts():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    stop_hook_timeout = hooks["Stop"][0]["hooks"][0]["timeout"]
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    tool_timeout = mcp["mcpServers"]["reviewer-agent"]["tool_timeout_sec"]
    assert stop_hook_timeout <= 10
    assert tool_timeout > backend.STOP_REVIEW_TIMEOUT
    assert tool_timeout > backend.MAX_COMMIT_REVIEW_TIMEOUT


def test_session_start_never_launches_a_reviewer():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert "SessionStart" not in hooks


def test_plugin_root_paths_are_quoted():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [
        hook["command"]
        for groups in hooks.values()
        for group in groups
        for hook in group["hooks"]
    ]
    assert commands
    assert all('"${PLUGIN_ROOT}"' in command for command in commands)


def test_hook_commands_survive_evicted_plugin_cache(tmp_path):
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    cases = [
        (hooks["UserPromptSubmit"][0]["hooks"][0]["command"], "hook_prompt.py", []),
        (hooks["PreToolUse"][0]["hooks"][0]["command"], "hook_pre_tool.py", []),
        (hooks["PreToolUse"][1]["hooks"][0]["command"], "hook_pre_tool.py", []),
        (hooks["PostToolUse"][0]["hooks"][0]["command"], "hook_post_tool.py", []),
        (hooks["SubagentStart"][0]["hooks"][0]["command"], "hook_subagent.py", ["start"]),
        (hooks["SubagentStop"][0]["hooks"][0]["command"], "hook_subagent.py", ["stop"]),
        (hooks["Stop"][0]["hooks"][0]["command"], "hook_stop.py", []),
        (hooks["SessionEnd"][0]["hooks"][0]["command"], "hook_session_end.py", []),
    ]
    home = tmp_path / "home"
    evicted_root = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / "reviewer-agent-codex"
        / "0.1.0+codex.evicted"
    )
    scripts = home / "plugins" / "reviewer-agent-codex" / "scripts"
    scripts.mkdir(parents=True)
    for script_name in {script_name for _, script_name, _ in cases}:
        (scripts / script_name).write_text(
            "import pathlib,sys\n"
            "print(pathlib.Path(__file__).name + '|' + ','.join(sys.argv[1:]) + '|' + sys.stdin.read())\n",
            encoding="utf-8",
        )

    for command, script_name, arguments in cases:
        result = subprocess.run(
            command.replace("${PLUGIN_ROOT}", str(evicted_root)),
            input="payload",
            text=True,
            capture_output=True,
            shell=True,
            env=dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1"),
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}|{}|payload\n".format(
            script_name, ",".join(arguments)
        )


def test_hook_command_prefers_replacement_cache_over_source_fallback(tmp_path):
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    command = hooks["Stop"][0]["hooks"][0]["command"]
    home = tmp_path / "home"
    cache_root = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / "reviewer-agent-codex"
    )
    evicted_root = cache_root / "0.1.0+codex.evicted"
    replacement_script = cache_root / "0.1.0+codex.new" / "scripts" / "hook_stop.py"
    source_script = home / "plugins" / "reviewer-agent-codex" / "scripts" / "hook_stop.py"
    for script, marker in ((replacement_script, "cache"), (source_script, "source")):
        script.parent.mkdir(parents=True)
        script.write_text('print("{}")\n'.format(marker), encoding="utf-8")

    result = subprocess.run(
        command.replace("${PLUGIN_ROOT}", str(evicted_root)),
        text=True,
        capture_output=True,
        shell=True,
        env=dict(os.environ, HOME=str(home), PYTHONDONTWRITEBYTECODE="1"),
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "cache\n"


def test_repository_guidance_requires_tdd_and_amygdala():
    guidance = (ROOT / "AGENTS.md").read_text(encoding="utf-8").lower()
    assert "red" in guidance and "green" in guidance and "refactor" in guidance
    assert "amygdala" in guidance


def test_readme_documents_one_command_local_install():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python3 scripts/install_local.py" in readme
    assert "new Codex thread" in readme
    assert "gpt-5.6-luna" in readme
    assert '"reasoningEffort": "xhigh"' in readme
    assert "$reviewer-agent-codex:reviewer-model" in readme
    assert "$reviewer-agent-codex:reviewer-session" in readme
    assert "timed-out reviewer session" in " ".join(readme.split())
    assert '"commitReviewTimeoutSeconds": 3600' in readme
    assert '"reviewerSessionAuditLimit": 3' in readme
    assert "REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS" in readme
    assert '"reviewHistoryKeep": 100' in readme
    assert "REVIEWER_CODEX_REVIEW_HISTORY_KEEP" in readme
    assert "per-project history" in readme
    assert "7,200" in readme
    assert "descendant process groups" in " ".join(readme.split())
    assert "direct_only_tool_namespaces" in readme
    assert "cannot yield" in readme


def test_reviewer_model_skill_is_packaged_with_namespaced_invocation():
    skill = ROOT / "skills" / "reviewer-model"
    instructions = (skill / "SKILL.md").read_text(encoding="utf-8")
    metadata = (skill / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "[TODO" not in instructions
    assert (skill / "scripts" / "toggle.py").is_file()
    assert "$reviewer-agent-codex:reviewer-model" in metadata


def test_reviewer_session_skill_is_packaged_with_namespaced_invocation():
    skill = ROOT / "skills" / "reviewer-session"
    instructions = (skill / "SKILL.md").read_text(encoding="utf-8")
    metadata = (skill / "agents" / "openai.yaml").read_text(encoding="utf-8")
    assert "UserPromptSubmit" in instructions
    assert "without a model request" in instructions
    assert "do not run" in instructions.lower()
    assert "$reviewer-agent-codex:reviewer-session" in metadata


def test_all_skill_prompts_use_plugin_namespaced_invocations():
    metadata_files = sorted((ROOT / "skills").glob("*/agents/openai.yaml"))
    assert metadata_files
    for path in metadata_files:
        metadata = path.read_text(encoding="utf-8")
        assert "$reviewer-agent-codex:" in metadata, path


def test_reviewer_brief_template_uses_namespaced_skill_invocation():
    template = (ROOT / "reviewerAgent.md.template").read_text(encoding="utf-8")
    assert "$reviewer-agent-codex:reviewer-agent on" in template
    assert "`$reviewer-agent on`" not in template


def test_plugin_packages_the_same_disclaimer_as_the_repository_root():
    plugin_notice = (ROOT / "DISCLAIMER.md").read_bytes()
    repository_notice = (ROOT.parent.parent / "DISCLAIMER.md").read_bytes()
    assert plugin_notice == repository_notice


def test_toggle_skills_use_prompt_hook_without_shell_write():
    for name in ("reviewer-agent", "stop-continue"):
        instructions = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert "UserPromptSubmit" in instructions
        assert "without a model request" in instructions
        assert "do not run the toggle script" in instructions
