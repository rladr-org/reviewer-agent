# SPDX-License-Identifier: MPL-2.0
import json
import time

from reviewer_codex import liveness


def test_shell_and_subagent_liveness(git_repo, isolated_state):
    liveness.mark(git_repo, "root", "shell", "tool-1", "pytest")
    liveness.mark(git_repo, "root", "subagent", "agent-1", "reviewer")
    items = liveness.outstanding(git_repo, "root")
    assert {item["kind"] for item in items} == {"shell", "subagent"}
    liveness.clear(git_repo, "root", "shell", "tool-1")
    assert [item["kind"] for item in liveness.outstanding(git_repo, "root")] == ["subagent"]
    liveness.clear_session("root")
    assert liveness.outstanding(git_repo, "root") == []


def test_stale_marker_is_pruned(git_repo, isolated_state, monkeypatch):
    monkeypatch.setenv("REVIEWER_LIVENESS_TTL_SECONDS", "60")
    liveness.mark(git_repo, "root", "shell", "tool-1", "old")
    path = next((isolated_state / "liveness").rglob("*.json"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["time"] = time.time() - 61
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert liveness.outstanding(git_repo, "root") == []


def test_shell_challenge_matches_only_the_previously_seen_marker_set(git_repo, isolated_state):
    first = {"kind": "shell", "id": "tool-1", "label": "first"}
    second = {"kind": "shell", "id": "tool-2", "label": "second"}

    assert not liveness.shells_were_challenged(git_repo, "root", [first])
    liveness.challenge_shells(git_repo, "root", [first, second])

    assert liveness.shells_were_challenged(git_repo, "root", [first, second])
    assert liveness.shells_were_challenged(git_repo, "root", [second])
    assert not liveness.shells_were_challenged(git_repo,
        "root", [first, second, {"kind": "shell", "id": "tool-3"}]
    )

    liveness.clear_session("root")
    assert not liveness.shells_were_challenged(git_repo, "root", [first])


def test_background_challenge_matches_exact_shell_and_subagent_marker_set(
    git_repo, isolated_state,
):
    shell = {"kind": "shell", "id": "tool-1", "label": "validator"}
    subagent = {"kind": "subagent", "id": "agent-1", "label": "default"}

    assert not liveness.items_were_challenged(git_repo, "root", [shell, subagent])
    liveness.challenge_items(git_repo, "root", [shell, subagent])

    assert liveness.items_were_challenged(git_repo, "root", [shell, subagent])
    assert liveness.items_were_challenged(git_repo, "root", [subagent])
    assert not liveness.items_were_challenged(git_repo,
        "root",
        [shell, subagent, {"kind": "subagent", "id": "agent-2"}],
    )


def test_background_challenge_reads_legacy_shell_only_state(git_repo, isolated_state):
    path = liveness._challenge_path(git_repo, "root")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"ids": ["tool-1"], "time": time.time()}), encoding="utf-8")

    assert liveness.items_were_challenged(git_repo,
        "root", [{"kind": "shell", "id": "tool-1"}]
    )
    assert not liveness.items_were_challenged(git_repo,
        "root", [{"kind": "subagent", "id": "agent-1"}]
    )


def test_clear_items_removes_only_ticketed_shell_and_subagent_markers(git_repo, isolated_state):
    liveness.mark(git_repo, "root", "shell", "tool-1", "validator")
    liveness.mark(git_repo, "root", "subagent", "agent-1", "default")
    liveness.mark(git_repo, "root", "subagent", "agent-2", "new worker")
    ticketed = [
        {"kind": "shell", "id": "tool-1"},
        {"kind": "subagent", "id": "agent-1"},
    ]
    liveness.challenge_items(git_repo, "root", ticketed)

    liveness.clear_items(git_repo, "root", ticketed)

    assert [(item["kind"], item["id"]) for item in liveness.outstanding(git_repo, "root")] == [
        ("subagent", "agent-2")
    ]
    assert not liveness.items_were_challenged(git_repo, "root", ticketed)


def test_describe_bounds_large_marker_lists(isolated_state):
    items = [
        {"kind": "shell", "id": "tool-{}".format(index), "label": "command {}".format(index)}
        for index in range(20)
    ]

    lines = liveness.describe(items, limit=8)

    assert len(lines) == 9
    assert lines[0] == "shell command: command 0"
    assert lines[-1] == "... and 12 more background items"
