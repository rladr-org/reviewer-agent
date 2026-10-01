# SPDX-License-Identifier: MPL-2.0
"""Both distributions load the same resource, including an updated installation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = Path("plugins/reviewer-agent-codex")
RESOURCE = Path("prompts/stop_supervisor.txt")


def _read_prompts(root, env, claude=True):
    code = (
        "import json, sys; "
        "sys.path.insert(0, sys.argv[1]); "
        "from reviewer_codex import prompts; "
    )
    if claude:
        code += (
            "sys.path.insert(0, sys.argv[2]); import reviewer_prompts; "
            "print(json.dumps([prompts.STOP_BRIEF, "
            "reviewer_prompts.STOP_SUPERVISE_PROMPT.format('DRAFT')]))"
        )
    else:
        code += "print(json.dumps([prompts.STOP_BRIEF]))"
    result = subprocess.run(
        [sys.executable, "-c", code, str(root / PLUGIN), str(root)],
        cwd=str(root), env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def test_both_adapters_read_one_updated_resource(tmp_path):
    assert (ROOT / PLUGIN / RESOURCE).is_file(), "one packaged canonical prompt is required"
    for source in ROOT.glob("reviewer_*.py"):
        shutil.copy2(source, tmp_path / source.name)
    shutil.copytree(ROOT / PLUGIN, tmp_path / PLUGIN,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    resource = tmp_path / PLUGIN / RESOURCE
    original = resource.read_text(encoding="utf-8").strip()
    env = dict(os.environ, REVIEWER_HOME=str(tmp_path / "state"),
               REVIEWER_CODEX_STATE_DIR=str(tmp_path / "codex-state"))
    codex, claude = _read_prompts(tmp_path, env)
    assert codex == original
    assert claude == original + "\n\nThe agent's proposed final message:\n\nDRAFT"
    updated = original + "\n\nShared policy update for this isolated installation."
    resource.write_text(updated + "\n", encoding="utf-8")
    codex, claude = _read_prompts(tmp_path, env)
    assert codex == updated
    assert claude == updated + "\n\nThe agent's proposed final message:\n\nDRAFT"


def test_codex_distribution_loads_without_parent_repository(tmp_path):
    assert (ROOT / PLUGIN / RESOURCE).is_file()
    shutil.copytree(ROOT / PLUGIN, tmp_path / PLUGIN,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    env = dict(os.environ, REVIEWER_HOME=str(tmp_path / "state"),
               REVIEWER_CODEX_STATE_DIR=str(tmp_path / "codex-state"))
    assert _read_prompts(tmp_path, env, claude=False) == [
        (tmp_path / PLUGIN / RESOURCE).read_text(encoding="utf-8").strip()
    ]


def test_the_prompt_treats_operator_only_steps_as_a_hand_off():
    # Task 11 (#19): a slash command or host restart is the operator's to do; a final message
    # that asks for it is a proper hand-off, not unfinished agent work.
    text = (ROOT / PLUGIN / RESOURCE).read_text(encoding="utf-8")
    assert "OPERATOR-ONLY STEPS" in text
    assert "slash command" in text


def test_the_output_rule_says_any_other_reply_keeps_the_agent_working():
    # Observed 2026-09-29: the supervisor wrote "stopping is appropriate" without the sentinel, and
    # the hook relayed that to the agent as a keep-working directive.
    text = (ROOT / PLUGIN / RESOURCE).read_text(encoding="utf-8")
    assert "instruction to keep working" in text
    assert "stopping is appropriate" in text


def test_the_output_rule_says_only_the_agent_reads_a_directive():
    # Observed 2026-09-30, seven times: the supervisor wrote to the operator ("Should the agent add
    # ... to the to-do list? The `--init` change still needs your decision") and the hook relayed
    # that to the agent as a keep-working directive it could do nothing with.
    text = " ".join((ROOT / PLUGIN / RESOURCE).read_text(encoding="utf-8").split())
    assert "only the agent reads a directive" in text.lower()
    assert "never write to the operator" in text.lower()


def test_work_promised_in_earlier_replies_counts_as_remaining_work():
    # Replayed live 2026-09-30: shown the AGENT'S OWN EARLIER REPLIES block with "3. Recording the
    # Linux to-dos ... in HANDOVER.md" undone, the supervisor still approved the stop (2 of 2),
    # because its remaining-work rule only read the proposed final message.
    text = " ".join((ROOT / PLUGIN / RESOURCE).read_text(encoding="utf-8").split())
    assert "AGENT'S OWN EARLIER REPLIES" in text
    assert "counts as remaining work" in text
