# SPDX-License-Identifier: MPL-2.0
"""The CI configuration covers what the release plan says it covers.

CI is the only check that keeps running after the release, so its coverage claims are worth
pinning: a matrix that quietly lost its Python floor, an action that drifted from a pinned SHA
to a mutable tag, or a privacy scan that stopped running would each be invisible until the
thing they prevent has already happened.

These tests read the workflow files. They do not run them -- that is GitHub's job -- so they
assert the presence of the jobs, versions and commands the plan requires, not their outcome.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

yaml = pytest.importorskip("yaml")


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def workflow_text():
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(WORKFLOWS.glob("*.yml"))
    )


# --------------------------------------------------------------------------
# the Python matrix
# --------------------------------------------------------------------------
def test_the_python_matrix_covers_the_floor_and_current_on_both_platforms():
    workflow = load("python.yml")
    matrix = workflow["jobs"]["root-suite"]["strategy"]["matrix"]["include"]
    combinations = {(entry["os"], str(entry["python"])) for entry in matrix}

    ubuntu = {python for os_name, python in combinations if os_name.startswith("ubuntu")}
    mac = {python for os_name, python in combinations if os_name.startswith("macos")}

    assert {"3.9", "3.12", "3.14"} <= ubuntu, combinations
    # macOS is the manually validated platform and must not end up with strictly less coverage
    # than Linux: a current interpreter here, and the 3.9 floor in the system-interpreter job.
    assert mac, combinations
    assert "macos-system-python" in workflow["jobs"]


def test_the_macos_floor_runs_on_the_system_interpreter():
    # /usr/bin/python3 IS the documented macOS install path -- the reason the runtime is
    # standard-library-only. setup-python would test an interpreter no user needs to have, and
    # no longer publishes 3.9 for a supported macOS image in any case.
    job = load("python.yml")["jobs"]["macos-system-python"]
    commands = " ".join(str(step.get("run", "")) for step in job["steps"])

    assert "/usr/bin/python3" in commands
    assert commands.count("pytest -p no:cacheprovider") >= 2


def test_the_system_interpreter_check_pins_the_floor_exactly():
    # `>=` would keep passing after the image ships a newer interpreter, and the 3.9 floor the
    # README promises would stop being exercised anywhere on macOS without anyone noticing.
    commands = " ".join(
        str(step.get("run", ""))
        for step in load("python.yml")["jobs"]["macos-system-python"]["steps"]
    )

    assert "!= (3, 9)" in commands
    assert ">= (3, 9)" not in commands
    # And it has to say what to do about it, or the failure is just noise on image-update day.
    assert "host_versions.py" in commands


def test_the_structure_job_checks_out_full_history():
    # The privacy scan resolves 40-hex strings against local history to catch a quoted private
    # commit id. A shallow checkout cannot resolve older ids, so the check would pass by
    # failing to look.
    checkout = load("python.yml")["jobs"]["structure"]["steps"][0]

    assert checkout["with"]["fetch-depth"] == 0


def test_no_workflow_targets_a_retired_runner_image():
    # macos-13 was retired by GitHub; a job pinned to it never starts, and a matrix leg that
    # never starts is indistinguishable from one that passes.
    text = workflow_text()

    for retired in ("macos-13", "macos-12", "macos-11", "ubuntu-20.04", "ubuntu-18.04"):
        assert retired not in text, retired


def test_both_canonical_suites_run_in_ci():
    steps = load("python.yml")["jobs"]["root-suite"]["steps"]
    commands = " ".join(str(step.get("run", "")) for step in steps)

    assert "python -m pytest -p no:cacheprovider" in commands
    assert any(
        step.get("working-directory") == "plugins/reviewer-agent-codex" for step in steps
    )


def test_ci_fails_when_the_suites_leave_artifacts_behind():
    text = (WORKFLOWS / "python.yml").read_text(encoding="utf-8")

    assert "git status --porcelain" in text
    assert "left the working tree dirty" in text


def test_the_structure_job_runs_every_release_check():
    steps = load("python.yml")["jobs"]["structure"]["steps"]
    commands = " ".join(str(step.get("run", "")) for step in steps)

    for tool in ("check_split_layering.py", "release_versions.py", "scan_release.py",
                 "release_export.py"):
        assert tool in commands, tool
    # The export is scanned separately from the repository: the point of the export is that it
    # contains less, so scanning only the source would never prove that.
    assert "--path /tmp/export" in commands
    assert "reproducible" in commands


# --------------------------------------------------------------------------
# the Pi extension
# --------------------------------------------------------------------------
def test_the_pi_job_uses_the_lockfile_and_a_real_git_identity():
    job = load("pi-extension.yml")["jobs"]["git-read-only"]
    commands = " ".join(str(step.get("run", "")) for step in job["steps"])

    assert "npm ci" in commands
    assert "npm install" not in commands
    # The live suite commits into repositories it builds; git refuses without an identity.
    assert "user.email" in commands and "user.name" in commands
    assert "test/run.mjs" in commands and "test/live.mjs" in commands


def test_the_pi_job_covers_the_declared_node_floor():
    versions = {
        str(value)
        for value in load("pi-extension.yml")["jobs"]["git-read-only"]["strategy"]["matrix"]["node"]
    }

    assert "22.19" in versions


def test_the_live_pi_tests_are_actually_enabled():
    steps = load("pi-extension.yml")["jobs"]["git-read-only"]["steps"]

    assert any(step.get("env", {}).get("PI_GIT_READ_LIVE") == "1" for step in steps)


# --------------------------------------------------------------------------
# supply chain
# --------------------------------------------------------------------------
def test_every_action_is_pinned_to_a_commit_sha():
    uses = re.findall(r"uses:\s*(\S+)", workflow_text())

    assert uses
    for reference in uses:
        _, _, version = reference.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", version), reference


def test_no_workflow_interpolates_untrusted_event_data_into_a_shell():
    # Workflow command injection: `${{ github.event.* }}` inside `run:` is the classic vector.
    text = workflow_text()

    assert "github.event." not in text


def test_workflows_request_only_read_permissions():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert parsed.get("permissions") == {"contents": "read"}, path.name


def test_the_network_dependent_audit_is_scheduled_not_a_pull_request_gate():
    # A registry outage must not block every merge.
    triggers = load("vulnerability-scan.yml")[True]  # PyYAML parses the `on:` key as True

    assert "schedule" in triggers
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers


def test_dependabot_watches_all_three_ecosystems_weekly():
    updates = yaml.safe_load(
        (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    )["updates"]
    ecosystems = {entry["package-ecosystem"]: entry for entry in updates}

    assert set(ecosystems) == {"pip", "npm", "github-actions"}
    for entry in updates:
        assert entry["schedule"]["interval"] == "weekly"
    assert ecosystems["npm"]["directory"] == "/pi-extensions/git-read-only"
