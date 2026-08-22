# SPDX-License-Identifier: MPL-2.0
"""The public-facing policy documents say the things a user has to be told.

Not a style check. Each assertion here corresponds to a claim someone could otherwise make a
decision on and be wrong: that the reviewer only reads (it executes, on one backend), that the
project runs a service (it does not), that review history is bounded and unremovable (it is
bounded and removable), or that Linux is as validated as macOS (it is not).

The documents are prose and will be rewritten. What is pinned is the FACT, not the sentence:
each check looks for the specific term or path that carries the claim, so a rewrite that drops
the claim fails and a rewrite that only rephrases it does not.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def read(name):
    return (ROOT / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# the documents exist
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["SECURITY.md", "PRIVACY.md", "CONTRIBUTING.md", "CHANGELOG.md"])
def test_the_public_policy_documents_exist_and_are_substantive(name):
    text = read(name)

    assert len(text) > 1000, name


# --------------------------------------------------------------------------
# SECURITY.md: the adversarial framing, not a reporting address alone
# --------------------------------------------------------------------------
def test_security_names_the_private_reporting_channel_and_no_email():
    text = read("SECURITY.md")

    assert "private vulnerability reporting" in text.lower()
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), "no address is published"


def test_security_states_that_repository_content_is_attacker_controlled():
    text = read("SECURITY.md").lower()

    assert "attacker-controlled" in text
    assert "prompt-injection" in text or "prompt injection" in text


def test_security_distinguishes_the_two_execution_boundaries():
    text = read("SECURITY.md")

    # Codex executes; the sandbox and approval policy are the specifics that make that concrete.
    assert "workspace-write" in text
    assert 'approval_policy="never"' in text
    # Pi does not.
    assert "no shell" in text.lower()
    assert "git_read" in text
    assert "does not install, launch, or fall back to Codex" in text


def test_security_names_the_mitigations_that_bound_execution():
    text = read("SECURITY.md").lower()

    for mitigation in ("timeout", "cancellation", "process-tree", "sandbox", "allowlist"):
        assert mitigation in text, mitigation


def test_security_says_which_way_each_path_fails():
    text = read("SECURITY.md").lower()

    assert "fail-closed" in text
    assert "fail-open" in text


# --------------------------------------------------------------------------
# PRIVACY.md: what leaves, what stays, and how to remove it
# --------------------------------------------------------------------------
def test_privacy_denies_operating_a_service_and_any_telemetry():
    text = read("PRIVACY.md").lower()

    assert "no service" in text or "operates no service" in text
    assert "telemetry" in text
    assert "opt-in" in text


def test_privacy_states_what_each_call_sends():
    text = read("PRIVACY.md")

    for item in ("git diff --cached", "reviewerAgent.md", "/reviewerDirective",
                 "AskUserQuestion", "plan and task list"):
        assert item in text, item


def test_privacy_names_every_storage_location():
    text = read("PRIVACY.md")

    for path in ("~/.claude/reviewer-agent/", "~/.codex/reviewer-agent-codex/",
                 "logs/reviewer.log", "REVIEWER_STATE_DIR", "REVIEWER_CODEX_STATE_DIR"):
        assert path in text, path


def test_privacy_documents_retention_and_both_purge_commands():
    text = read("PRIVACY.md")

    assert "100" in text
    assert "oldest-first" in text
    assert "/reviewerHistory clear" in text
    assert "$reviewer-agent-codex:reviewer-history clear" in text
    assert "plaintext" in text.lower()


# --------------------------------------------------------------------------
# CHANGELOG.md: the version discontinuity, explained where people will look
# --------------------------------------------------------------------------
def test_the_changelog_explains_the_missing_public_history():
    text = read("CHANGELOG.md")

    assert "1.2.0-beta.1" in text
    assert "first public release" in text.lower()
    assert "developed privately" in text.lower()
    # The two version lines it unifies, so the jump does not read as an error.
    assert "1.1.3" in text and "0.1.0" in text


# --------------------------------------------------------------------------
# issue templates
# --------------------------------------------------------------------------
TEMPLATES = ("defect.yml", "installation.yml", "platform.yml", "documentation.yml")


@pytest.mark.parametrize("name", TEMPLATES)
def test_each_issue_template_is_valid_and_asks_for_versions(name):
    # PyYAML is a development dependency, not a runtime one. Skipping without it keeps the
    # suite runnable on a bare interpreter; CI installs requirements-dev.txt, so the syntax of
    # every template is checked there -- GitHub silently ignores a malformed template.
    yaml = pytest.importorskip("yaml")
    path = ROOT / ".github" / "ISSUE_TEMPLATE" / name
    text = path.read_text(encoding="utf-8")

    parsed = yaml.safe_load(text)
    assert parsed["name"], name
    assert parsed["body"], name
    if name != "documentation.yml":
        assert "version" in text.lower(), name


def test_the_template_config_routes_security_reports_privately():
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8")

    assert "security/advisories/new" in text
    assert "SECURITY.md" in text
