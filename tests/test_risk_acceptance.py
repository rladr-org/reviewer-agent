# SPDX-License-Identifier: MPL-2.0
import subprocess

import reviewer_lib as rl


def test_risk_acceptance_is_bound_to_project_and_current_disclaimer(
    tmp_path, monkeypatch
):
    home = tmp_path / "reviewer-home"
    monkeypatch.setenv("REVIEWER_HOME", str(home))
    first = tmp_path / "first"
    second = tmp_path / "second"
    for repo in (first, second):
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)

    rl.record_risk_acceptance(first, "first-hash", "manual-slash-command")

    assert rl.risk_accepted(first, "first-hash")
    assert not rl.risk_accepted(first, "changed-hash")
    assert not rl.risk_accepted(second, "first-hash")
