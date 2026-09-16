# SPDX-License-Identifier: MPL-2.0
"""The release matrix checks the real MCP inventory without launching a review."""
import os

import pytest

from tools import release_matrix


def test_release_matrix_accepts_only_commit_and_lifecycle_tools(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(release_matrix.CODEX_PLUGIN))
    from reviewer_codex import state

    monkeypatch.setenv("REVIEWER_CODEX_STATE_DIR", str(tmp_path / "state"))
    repo = release_matrix.make_repo(tmp_path / "repo", "inventory")

    class InventoryChecked(Exception):
        pass

    class Result(release_matrix.Result):
        def check(self, description, passed, detail=""):
            super().check(description, passed, detail)
            if "tools" in description:
                raise InventoryChecked

    # Guard against accidentally extending this test into real commit reviews.
    original = release_matrix.McpClient.call
    def call(client, method, *args, **kwargs):
        assert method in ("initialize", "tools/list"), "unexpected model review in inventory test"
        return original(client, method, *args, **kwargs)
    monkeypatch.setattr(release_matrix.McpClient, "call", call)
    result = Result("inventory")
    with pytest.raises(InventoryChecked):
        release_matrix._codex_scenario_body(result, repo, tmp_path, tmp_path / "state",
                                           dict(os.environ), state)
    assert len(result.checks) == 3
    assert all(passed for _, passed, _ in result.checks), result.checks
