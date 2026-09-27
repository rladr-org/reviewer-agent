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
