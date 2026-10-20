# SPDX-License-Identifier: MPL-2.0
"""What ships publicly, what never does, and the scanner that enforces the difference.

The export is the release. A file that reaches it is published irreversibly, so two properties
are pinned here rather than trusted:

  EXCLUSION IS EXPLICIT. Internal release planning is tracked on purpose -- the commit gate
  reviews a plan before the code implementing it -- and must still never be published. Deriving
  that from `.gitignore` would make publication depend on ignore rules written for a different
  purpose; `docs/plans/` ships and `docs/internal/` does not, and they differ by one line.

  THE SCANNER CATCHES WHAT IT CLAIMS TO. A privacy scanner nobody has tried to defeat is a
  scanner that passes because its patterns never match anything, including real leaks. Every
  category it advertises is exercised against synthetic content below.
"""
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import release_export as rex  # noqa: E402
import scan_release as scanner  # noqa: E402
from repo_context import IS_REPOSITORY, requires_repository  # noqa: E402


# --------------------------------------------------------------------------
# the exclusion list
# --------------------------------------------------------------------------
@requires_repository
def test_the_internal_planning_documents_are_excluded_by_name():
    assert "PUBLIC_BETA_RELEASE_PLAN.md" in rex.EXCLUDED_PATHS
    assert "PUBLIC_BETA_RELEASE_PLAN_REVIEW.md" in rex.EXCLUDED_PATHS
    for name in rex.EXCLUDED_PATHS:
        assert rex.is_excluded(name)
        assert name not in rex.included_files()


def test_internal_docs_and_the_private_term_list_are_excluded_by_pattern():
    assert rex.is_excluded("docs/internal/HANDOVER.md")
    assert rex.is_excluded("tools/private_terms.txt")
    # ...and the tracked plans directory is NOT, because it ships deliberately.
    assert not rex.is_excluded("docs/plans/reviewer_model_pi.md")


@requires_repository
def test_no_handover_ever_ships():
    # A handover is the operator's working log: machine paths, environment names, session ids.
    for name in ("HANDOVER.md", "docs/HANDOVER-2026-10.md", "plugins/reviewer-agent-codex/HANDOVER.md",
                 "docs/plans/handover_notes.md"):
        assert rex.is_excluded(name), name
    assert not [name for name in rex.included_files() if "handover" in name.lower()]
    assert "HANDOVER.md" not in rex.tracked_files()


@requires_repository
def test_the_shipping_list_is_deterministic_and_non_empty():
    first = rex.included_files()

    assert first == sorted(first)
    assert len(first) > 100
    assert first == rex.included_files()


# --------------------------------------------------------------------------
# building an export
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def export(tmp_path_factory):
    if not IS_REPOSITORY:
        pytest.skip("building an export needs a Git working tree")
    target = tmp_path_factory.mktemp("export") / "tree"
    rex.export(target)
    return target


@requires_repository
def test_the_export_contains_every_required_file_and_no_excluded_one(export):
    assert rex.verify(export) == []
    assert "DISCLAIMER.md" in rex.REQUIRED_PATHS
    for name in rex.REQUIRED_PATHS:
        assert (export / name).is_file(), name
    for name in rex.EXCLUDED_PATHS:
        assert not (export / name).exists(), name


@requires_repository
def test_the_export_carries_no_git_directory_or_runtime_state(export):
    assert not (export / ".git").exists()
    for forbidden in ("__pycache__", ".pytest_cache", "state", "logs", "reviews"):
        assert not list(export.rglob(forbidden)), forbidden


@requires_repository
def test_exported_codex_server_starts_and_exposes_both_review_paths(export, tmp_path):
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    result = subprocess.run(
        [sys.executable, str(export / "plugins/reviewer-agent-codex/scripts/mcp_server.py")],
        input="".join(json.dumps(item) + "\n" for item in requests),
        capture_output=True, text=True, cwd=tmp_path, timeout=15,
        env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
    )
    assert result.returncode == 0, result.stderr
    responses = {item["id"]: item for item in map(json.loads, result.stdout.splitlines())}
    assert "serverInfo" in responses[1]["result"]
    names = {item["name"] for item in responses[2]["result"]["tools"]}
    assert {"review_commit", "stop_hook", "interrupt_stop"} <= names


@requires_repository
def test_export_verifier_rejects_missing_codex_git_module(export, tmp_path):
    import shutil

    broken = tmp_path / "broken-export"
    shutil.copytree(export, broken)
    module = "plugins/reviewer-agent-codex/reviewer_codex/git.py"
    (broken / module).unlink(missing_ok=True)
    assert "required file missing: " + module in rex.verify(broken)


def test_the_export_refuses_to_overwrite_a_non_empty_directory(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "something.txt").write_text("existing\n", encoding="utf-8")

    with pytest.raises(SystemExit):
        rex.export(target)


@requires_repository
def test_the_archive_is_byte_reproducible(tmp_path, export):
    first = rex.write_archive(export, tmp_path / "one.tar.gz")
    second = rex.write_archive(export, tmp_path / "two.tar.gz")

    assert Path(first).read_bytes() == Path(second).read_bytes()
    with tarfile.open(str(first), "r:gz") as archive:
        members = archive.getmembers()
    assert members
    assert [item.name for item in members] == sorted(item.name for item in members)
    assert {item.mtime for item in members} == {0}
    assert {item.uid for item in members} == {0}


# --------------------------------------------------------------------------
# the scanner
# --------------------------------------------------------------------------
@requires_repository
def test_the_repository_is_clean_today():
    # The scan the release depends on, run against the tree that will become the release.
    assert scanner.scan(ROOT, check_commits=False) == []


@requires_repository
def test_the_built_export_is_clean(export):
    assert scanner.scan(export, check_commits=False) == []


# Every probe below is ASSEMBLED at runtime rather than written out. A file that spelled a
# token, a private-key header or a real-looking home path would be tracked content that the
# scanner must then flag -- so a literal fixture here would make this suite fail its own
# `test_the_repository_is_clean_today`, and would ship the specimen publicly besides.
ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _leak(kind):
    if kind == "home path (Users)":
        return "path = /" + "Users" + "/somebody/projects/thing\n"
    if kind == "home path (home)":
        return "path = /" + "home" + "/somebody/thing\n"
    if kind == "home path (user)":
        # `user` is a REAL account name on plenty of machines, not a documented fixture.
        return "path = /" + "Users" + "/user/thing\n"
    if kind == "email address":
        return "contact: someone" + "@" + "somecompany.com\n"
    if kind == "openai key":
        return "token = sk" + "-" + ALPHABET + "012345\n"
    if kind == "anthropic key":
        return "key = sk" + "-ant-" + ALPHABET + "\n"
    if kind == "github token":
        return "token = ghp" + "_" + ALPHABET + "0123\n"
    if kind == "github pat":
        return "token = github" + "_pat_" + ALPHABET + "\n"
    if kind == "aws key":
        return "aws = AKIA" + "IOSFODNN7EXAMPLE\n"
    if kind == "slack token":
        return "slack = xoxb" + "-1234567890-abcdefghij\n"
    if kind == "private key":
        return "-----BEGIN " + "OPENSSH PRIVATE KEY" + "-----\n"
    raise AssertionError(kind)


@pytest.mark.parametrize("probe,kind", [
    ("home path (Users)", "home path"),
    ("home path (home)", "home path"),
    ("home path (user)", "home path"),
    ("email address", "email address"),
    ("openai key", "openai key"),
    ("anthropic key", "anthropic key"),
    ("github token", "github token"),
    ("github pat", "github token"),
    ("aws key", "aws key"),
    ("slack token", "slack token"),
    ("private key", "private key"),
])
def test_every_advertised_category_actually_matches(tmp_path, probe, kind):
    (tmp_path / "leak.txt").write_text(_leak(probe), encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=False)

    assert [item[2] for item in findings] == [kind], findings


def test_no_finding_echoes_the_thing_it_found(tmp_path):
    # Scanner output lands in CI logs, which are retained and frequently public. A report that
    # quotes the credential it found publishes it in the act of reporting it.
    secret = "sk" + "-" + ALPHABET + "987654"
    (tmp_path / "leak.txt").write_text("token = " + secret + "\n", encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=False)

    assert findings and findings[0][2] == "openai key"
    assert secret not in str(findings)
    assert "redacted" in findings[0][3]


@pytest.mark.parametrize("content", [
    "PROJ = \"/Users/x/proj\"\n",
    "runner_home = /home/runner/work\n",
    "email = test@example.com\n",
    "url = https://github.com/rladr-org/reviewer-agent\n",
    "dependency = @earendil-works/pi-ai\n",
])
def test_documented_fixtures_are_permitted(tmp_path, content):
    # A scanner that flags its own project's fixtures gets disabled, and then it protects
    # nothing at all.
    (tmp_path / "fixture.py").write_text(content, encoding="utf-8")

    assert scanner.scan(tmp_path, check_commits=False) == []


def test_only_documented_synthetic_account_names_are_allowlisted():
    assert scanner.ALLOWED_HOME_NAMES == {"x", "runner"}


def test_generated_artifacts_are_refused_by_path(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "module.cpython-312.pyc").write_bytes(b"\x00")

    findings = scanner.scan(tmp_path, check_commits=False)

    assert any(item[2] == "forbidden path" for item in findings), findings


def test_runtime_state_directories_are_refused(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "session.codex").write_text("thread-1\n", encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=False)

    assert any(item[2] == "runtime state" for item in findings), findings


@requires_repository
def test_a_private_commit_identifier_is_refused(tmp_path):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (tmp_path / "notes.md").write_text("see commit {}\n".format(head), encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=True)

    assert any(item[2] == "private commit id" for item in findings), findings


def test_an_unrelated_hex_string_is_not_mistaken_for_a_commit(tmp_path):
    (tmp_path / "notes.md").write_text("blob {}\n".format("a" * 40), encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=True)

    assert findings == []


@requires_repository
def test_the_private_term_list_is_never_published():
    # The list names the things that must not be published; publishing the list would publish
    # them. It is untracked, excluded from the export, and its findings are redacted in output.
    assert rex.is_excluded("tools/private_terms.txt")
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "tools/private_terms.txt"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked == ""


def test_a_private_term_finding_redacts_the_term(tmp_path, monkeypatch):
    terms = tmp_path / "terms.txt"
    terms.write_text("supersecretcompany\n", encoding="utf-8")
    monkeypatch.setattr(scanner, "PRIVATE_TERMS_FILE", terms)
    target = tmp_path / "tree"
    target.mkdir()
    (target / "doc.md").write_text("built at supersecretcompany\n", encoding="utf-8")

    findings = scanner.scan(target, check_commits=False)

    assert [item[2] for item in findings] == ["private term"]
    assert "supersecretcompany" not in str(findings)


@requires_repository
def test_a_private_commit_finding_does_not_echo_the_identifier(tmp_path):
    head = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    (tmp_path / "notes.md").write_text("see commit {}\n".format(head), encoding="utf-8")

    findings = scanner.scan(tmp_path, check_commits=True)

    assert any(item[2] == "private commit id" for item in findings), findings
    assert head[:12] not in str(findings)
