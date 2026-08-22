# SPDX-License-Identifier: MPL-2.0
"""The public documentation contract: the claims a reader acts on, pinned.

This file is the README's specification, which is why a documentation rewrite has to change it
in the same commit rather than after. What is pinned is not prose but the handful of statements
someone makes a decision on and could be wrong about:

  the safety boundary   Codex executes commands; Pi cannot. A rewrite that blurs those is the
                        one documentation defect with a security consequence.
  the platform claim    manually validated, CI-only, unverified and unsupported are four
                        different things, and "tested" flattens them.
  the installers        two documents with distinct roles -- a cross-host handoff that may
                        invoke a Claude-specific procedure -- not two competing entry points.
  removal               a tool that installs commit hooks has to be removable, including the
                        steps no script performs for you.
"""
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CODEX_PLUGIN = ROOT / "plugins" / "reviewer-agent-codex"


def prose(text):
    """Markdown reduced to matchable prose: emphasis dropped, line wrapping collapsed.

    Documentation is hard-wrapped and emphasised, so a claim like "exercised by CI unit tests
    only" is genuinely present while a substring search for it fails. Matching the rendered
    sentence rather than its source keeps these tests pinning the CLAIM instead of the layout.
    """
    return " ".join(text.replace("*", "").replace("`", "").split())


def outside_code_blocks(text):
    """The document with fenced code blocks removed.

    `# Claude Code host` inside a bash block is a shell comment, not a heading; counting it as
    one would make the "exactly one top-level heading" check unsatisfiable.
    """
    kept, fenced = [], False
    for line in text.splitlines():
        if line.startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    return kept


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


def test_each_host_has_an_explicit_readme_install_route():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    codex_readme = (CODEX_PLUGIN / "README.md").read_text(encoding="utf-8")

    assert "## Claude Code installation" in root_readme
    assert "## Codex installation" in root_readme
    assert "plugins/reviewer-agent-codex/README.md" in root_readme
    assert "python3 plugins/reviewer-agent-codex/scripts/install_local.py" in codex_readme
    assert "codex plugin marketplace add rladr-org/reviewer-agent" in codex_readme


def test_agent_assisted_install_has_one_handoff_file_and_minimal_human_steps():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    codex_readme = (CODEX_PLUGIN / "README.md").read_text(encoding="utf-8")
    claude_agent_guide = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    handoff_path = ROOT / "INSTALL_WITH_AGENT.md"

    assert handoff_path.is_file()
    handoff = handoff_path.read_text(encoding="utf-8")

    assert "## Agent-assisted installation" in root_readme
    assert "INSTALL_WITH_AGENT.md" in root_readme
    assert "INSTALL_WITH_AGENT.md" in codex_readme
    assert "Start the developer host in the repository you want to protect." in root_readme
    assert (
        "Clone https://github.com/rladr-org/reviewer-agent.git into a new user-owned directory "
        "outside this target repository if needed, then read "
        "`INSTALL_WITH_AGENT.md` completely"
    ) in root_readme

    required_handoff_contract = (
        "## What the human must do",
        "## Procedure for the installing agent",
        "Do not treat the installer checkout as the target repository.",
        "Ask for all missing choices in one message.",
        "Do not ask the human to run a command that the agent can run.",
        "Human-only checkpoint",
        "approve writes outside the repository",
        "restart",
        "run `/hooks` and trust",
        "`AGENT_INSTALL.md`",
        "`plugins/reviewer-agent-codex/README.md`",
        "claude plugin marketplace add <INSTALLER_REPO>",
        "claude plugin install reviewer-agent@reviewer-agent",
        "python3 <INSTALLER_REPO>/toggle.py on",
        "<INSTALLER_REPO>/reviewerAgent.md.template",
        "python3 plugins/reviewer-agent-codex/scripts/install_local.py",
        "python3 <INSTALLER_REPO>/plugins/reviewer-agent-codex/scripts/toggle.py reviewer on",
        "<INSTALLER_REPO>/plugins/reviewer-agent-codex/reviewerAgent.md.template",
        "live gate verification is pending",
    )
    for text in required_handoff_contract:
        assert text in handoff

    assert "claude plugin marketplace add <PLUGIN_DIR>" in claude_agent_guide
    assert "claude plugin install reviewer-agent@reviewer-agent" in claude_agent_guide
    assert "For an agent-assisted installation" in claude_agent_guide


def test_linux_and_wsl2_beta_directions_cover_both_hosts_and_request_bug_reports():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    codex_readme = (CODEX_PLUGIN / "README.md").read_text(encoding="utf-8")
    agent_install = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")

    assert "### Native Linux (untested beta)" in root_readme
    assert "### Windows through WSL2 (untested beta)" in root_readme
    assert "Claude Code plugin" in root_readme
    assert "Codex plugin" in root_readme
    assert "curl -fsSL https://claude.ai/install.sh | bash" in root_readme
    assert "curl -fsSL https://chatgpt.com/codex/install.sh | sh" in root_readme
    assert "wsl --install" in root_readme
    assert 'echo "$WSL_DISTRO_NAME"' in root_readme
    assert "`~/code`" in root_readme
    assert "`/mnt/c`" in root_readme
    assert "Native Windows and WSL1 are not supported." in root_readme
    assert "Claude Code can run in WSL1" in root_readme
    assert "Codex 0.115" in root_readme

    for text in (root_readme, codex_readme):
        assert "expected to work" in text
        assert "https://github.com/rladr-org/reviewer-agent/issues" in text
        assert "wsl --list --verbose" in text
        assert "wsl --set-version <distribution-name> 2" in text
        assert "must show `2`" in text

    assert "### Native Linux (untested beta)" in codex_readme
    assert "### Windows through WSL2 (untested beta)" in codex_readme
    assert "run codex and the installer inside wsl2" in codex_readme.lower()
    assert 'echo "$WSL_DISTRO_NAME"' in codex_readme
    assert "Codex 0.115" in codex_readme

    assert "Darwin or Linux" in agent_install
    assert "Linux/WSL2 beta" in agent_install
    assert "grep -qi 'WSL2'" in agent_install
    assert "On any other system, **stop**" not in agent_install


def test_claude_hooks_resolve_python_from_path_in_exec_form():
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
    command_paths = tuple(sorted((ROOT / "commands").glob("*.md"))) + (
        ROOT / "skills" / "reviewerAgent" / "SKILL.md",
    )

    for path in command_paths:
        text = path.read_text(encoding="utf-8")
        assert "/usr/bin/python3" not in text, path
        assert "Bash(python3:*)" in text, path
        assert "!`python3 " in text, path


def test_readme_states_backend_capabilities_per_host_without_cross_host_ambiguity():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "Selecting `pi` in Claude Code routes commit audits, timed-out "
        "`AskUserQuestion` answers, and `stopContinue` through Pi."
    ) in readme
    assert "Selecting `pi` does not require or launch Codex." in readme
    assert "The Codex package currently supports no `pi` backend." in readme
    assert "The Codex package does not implement `AskUserQuestion`." in readme


def test_readme_avoids_conversational_filler():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    banned = (
        "the choice is yours to make",
        "that is enough to work",
        "worth doing next",
        "gone off the rails",
        "left to itself",
        "there is no neutral position here",
        "the one people skip",
    )

    for phrase in banned:
        assert phrase not in readme


def test_claude_distribution_surfaces_do_not_describe_pi_as_commit_only():
    paths = (
        ROOT / "AGENT_INSTALL.md",
        ROOT / "commands" / "reviewerAgent.md",
        ROOT / "skills" / "reviewerAgent" / "SKILL.md",
        ROOT / ".claude-plugin" / "plugin.json",
        ROOT / ".claude-plugin" / "marketplace.json",
        ROOT / "hooks" / "hooks.json",
    )
    stale_claims = (
        "pi backend gates commits only",
        "pi gates commits only",
        "question answering runs on codex whatever the configured backend",
        "question answering and stop supervision require codex",
        "the latter two require codex",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for claim in stale_claims:
            assert claim not in text, (path, claim)


def test_readme_warns_against_legacy_and_plugin_hook_duplication():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Do not keep legacy standalone hooks and plugin hooks enabled together." in readme
    assert "older standalone `SessionStart` hooks always launch Codex" in readme


def test_readme_distinguishes_codex_brief_and_commit_sandboxes():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert (
        "Briefing uses a read-only sandbox. Commit audits use `workspace-write` with "
        "`approval_policy=\"never\"`"
    ) in readme


def test_both_readmes_document_the_full_removal_path():
    """Uninstall instructions are load-bearing for a tool that installs hooks.

    A user who cannot cleanly remove this does not merely keep a plugin they dislike: they keep
    hooks that gate their commits. Each item below is a step that is genuinely easy to miss --
    the two the automated paths deliberately do NOT do (the Codex config array, the Claude
    timeout setting), the state directory that outlives the plugin, and the legacy standalone
    install whose hooks keep firing after the plugin is gone.
    """
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    codex_readme = (CODEX_PLUGIN / "README.md").read_text(encoding="utf-8")

    for fragment in (
        "claude plugin disable reviewer-agent",
        "claude plugin uninstall reviewer-agent",
        "claude plugin marketplace remove reviewer-agent",
        "rm -rf ~/.claude/reviewer-agent",
        "~/.pi/agent/extensions/git-read-only",
        "askUserQuestionTimeout",
        "/reviewerHistory clear",
    ):
        assert fragment in root_readme, fragment

    for fragment in (
        "scripts/uninstall_local.py",
        "--purge-state",
        "direct_only_tool_namespaces",
        "$reviewer-agent-codex:reviewer-history clear",
    ):
        assert fragment in codex_readme, fragment


def test_the_codex_uninstaller_exists_and_is_self_describing():
    script = CODEX_PLUGIN / "scripts" / "uninstall_local.py"

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    # It must say what it will NOT touch; that is the property that makes it safe to run.
    assert "leaving every other plugin and setting alone" in text


def test_the_readme_opens_with_the_authors_own_introduction():
    """The author's introduction leads the file, by explicit decision.

    It was moved below the reference material once and moved back: the opening is the author's
    to write, and it already answers what the tool is and which hosts it works with. What the
    reference section must NOT do is describe the same two capabilities over again -- that
    duplication is what made the original opening hard to read, not the introduction itself.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    lines = outside_code_blocks(readme)

    assert lines[0].strip() == "# What is it?"
    opening = prose(readme.split("# reviewer-agent", 1)[0])
    assert "Reviewer Agent" in opening and "Stop Continue" in opening
    reference = prose(readme.split("# reviewer-agent", 1)[1].split("## Host and", 1)[0])
    assert "Reference documentation" in reference
    assert "Two capabilities" not in reference


def test_the_safety_boundary_is_stated_before_any_installation_step():
    """Wherever the file begins, the boundary comes before EVERY route into installing.

    Ordering is the whole content of this test. A reader who follows an installation link
    without having been told that one backend executes commands in their repository has been
    misled by the document's shape, not by anything it says. The introduction carries its own
    `### installation` pointer, which is the first such route in the file and therefore the one
    the boundary has to precede -- checking only the later `## Agent-assisted installation`
    heading would have missed it, and did.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    boundary_at = readme.index("The safety boundary")
    routes = [
        readme.index("### installation"),
        readme.index("## Agent-assisted installation"),
        readme.index("## Claude Code installation"),
        readme.index("## Codex installation"),
    ]

    assert boundary_at < min(routes), "an installation route precedes the safety boundary"
    before_install = prose(readme[:min(routes)])
    assert "workspace-write" in before_install
    assert "no shell" in before_install
    assert "SECURITY.md" in before_install and "PRIVACY.md" in before_install


def test_the_backend_matrix_carries_the_execution_boundary():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    matrix = prose(readme.split("## Host and backend support", 1)[1].split("\n## ", 1)[0])

    assert 'approval_policy="never"' in matrix
    assert "git_read" in matrix
    assert "static audit" in matrix
    assert "no hidden Codex" in matrix or "no hidden codex" in matrix.lower()


def test_the_platform_claims_distinguish_validation_from_coverage():
    readme = prose((ROOT / "README.md").read_text(encoding="utf-8"))

    assert "manually validated end to end" in readme
    assert "exercised by CI unit tests only" in readme
    assert "unverified" in readme
    assert "Native Windows and WSL1 are not supported." in readme


def test_the_readme_links_every_public_policy_document():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    for document in ("SECURITY.md", "PRIVACY.md", "CONTRIBUTING.md", "CHANGELOG.md"):
        assert "({})".format(document) in readme, document


def test_the_readme_names_the_expected_failure_modes_and_where_to_look():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Expected failure modes and troubleshooting", 1)
    assert len(section) == 2, "the troubleshooting section is missing"
    body = prose(section[1].split("\n## ", 1)[0])

    # The five failures a first-run user actually hits, each with its remedy named.
    assert "/reviewerModel" in body
    assert "TIMED OUT" in body and "/reviewerModel timeout" in body
    assert "/reviewerSession rotate" in body
    assert "askUserQuestionTimeout" in body
    assert "logs/reviewer.log" in body


def test_the_troubleshooting_remedies_are_host_specific():
    """A Claude Code slash command does not exist in Codex, and the recovery behaviour is not
    the same either: a timed-out Codex reviewer is retired automatically, so telling those users
    to rotate by hand sends them to a control they do not have, for a problem already handled."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    body = prose(
        readme.split("## Expected failure modes and troubleshooting", 1)[1].split("\n## ", 1)[0]
    )

    # Every Claude-only remedy has its Codex counterpart named alongside it.
    assert "$reviewer-agent-codex:reviewer-model" in body
    assert "$reviewer-agent-codex:reviewer-session rotate" in body
    assert "REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS" in body
    # And the difference in timeout recovery is stated rather than glossed.
    assert "retires the timed-out reviewer automatically" in body
    # An out-of-range Codex timeout is SKIPPED, not clamped: it falls back to the default, so a
    # user who typed 10000 gets 3600 rather than 7200. Describing that as clamping would send
    # them looking for a limit that was applied to their value instead of a value that was
    # discarded.
    assert "60 to 7200" in body
    assert "skipped rather than clamped" in body
    assert "falls through" in body


def test_the_authors_introduction_is_intact_and_hands_over_to_the_installers():
    # The introduction is prose the author owns. What this pins is that all of it is still
    # there, in their own heading structure, and that it still ends by pointing at the
    # installation route rather than trailing off into the reference material.
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    introduction = readme.split("# reviewer-agent", 1)[0]

    for heading in (
        "# What is it?",
        "# Inspiration and Justification (which you may disagree with)",
        "# What is true",
        "## Plan and Code Review gates:",
        "## Don't stop - Continue!",
        "# Project state",
        "## A note about suggested workflow (not enforced by this tool)",
        "### installation",
    ):
        assert heading in introduction, heading
    assert "agent-assisted-installation" in introduction


def test_the_two_installer_documents_are_described_as_handoff_and_subprocedure():
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    handoff = (ROOT / "INSTALL_WITH_AGENT.md").read_text(encoding="utf-8")

    # One is the cross-host handoff; the other is the detailed Claude-specific procedure it
    # invokes. Presenting them as two entry points is what sends people down the wrong one.
    assert "INSTALL_WITH_AGENT.md" in root_readme
    assert "AGENT_INSTALL.md" in root_readme
    assert "`AGENT_INSTALL.md`" in handoff


def test_the_readme_says_what_the_reviewer_actually_does_before_installation():
    """Sandbox flags are not an explanation of behaviour.

    "workspace-write with approvals disabled" tells a reader who already knows Codex what the
    boundary is; it does not tell anyone what the agent DOES with it. The thing people need
    before installing is plain: the Codex reviewer explores the repository and runs your tests,
    the Pi reviewer reads the patch and cannot. Both belong ahead of the first install route.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first_route = min(
        readme.index("### installation"),
        readme.index("## Agent-assisted installation"),
    )
    before_install = prose(readme[:first_route])

    assert "What the reviewer actually does" in before_install
    # Codex: agentic, runs commands, cannot commit.
    assert "running commands in your repository" in before_install
    assert "test suite" in before_install
    assert "cannot create the commit" in before_install
    # Pi: static, no shell, cannot run tests.
    assert "static review" in before_install
    assert "cannot run your tests" in before_install
    # And the verdict contract a user will see either way.
    assert "APPROVED" in before_install and "REJECTED" in before_install
    # The session claim has to match the session contract further down the file: one reviewer
    # per project, developer session and role -- not one per project forever. An overstated
    # "briefed once per project" reads as a promise that rotation and a new host session both
    # break, and those are the two things that visibly reset a reviewer's memory.
    assert "per project, developer session and role" in before_install
    assert "/reviewerSession rotate" in before_install


def test_the_claude_backend_is_described_before_the_first_install_route():
    """The third backend answers "can I run this with only Claude Code installed" -- so the
    answer, and its two costs, belong where the other two backends are already explained.

    Its costs are not the same as theirs. It spends the operator's OWN Claude subscription
    quota rather than a separate provider's, and it cannot run their tests. Someone who learns
    either of those after installing learned it too late."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    first_route = min(
        readme.index("### installation"),
        readme.index("## Agent-assisted installation"),
    )
    before_install = prose(readme[:first_route])

    # Named as a choice, with what it spends and what it cannot do.
    assert "your own Claude subscription" in before_install
    assert "opt-in" in before_install
    assert "cannot run your tests" in before_install


def test_the_backend_matrix_states_the_claude_execution_boundary():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    matrix = prose(readme.split("## Host and backend support", 1)[1].split("\n## ", 1)[0])

    # The two flags that make this backend safe, and the reason it needs no git access.
    assert "--safe-mode" in matrix
    assert "dontAsk" in matrix
    assert "staged patch is included in the prompt" in matrix


def test_the_claude_backend_is_not_the_default_anywhere():
    """A backend that spends the operator's own subscription must never be selected FOR them.

    `codex` remains the fallback when nothing is configured; this pins the documentation to the
    same claim the resolver makes, so the two cannot drift apart silently."""
    readme = prose((ROOT / "README.md").read_text(encoding="utf-8"))

    assert "codex fallback" in readme
    assert "claude fallback" not in readme
    assert "Valid values are codex, pi and claude." in readme


def test_the_security_document_bounds_the_claude_backend():
    security = prose((ROOT / "SECURITY.md").read_text(encoding="utf-8"))

    # No hooks (measured recursion), fail-closed permissions, read-only tool set.
    assert "--safe-mode" in security
    assert "dontAsk" in security
    assert "Read, Grep and Glob" in security or "Read, Grep, Glob" in security


def test_the_privacy_document_says_where_a_claude_review_goes():
    privacy = prose((ROOT / "PRIVACY.md").read_text(encoding="utf-8"))

    assert "claude" in privacy.lower()
    # The two facts a reader cannot infer: whose account pays, and that the CLI authenticates
    # itself rather than this package handling any credential.
    assert "your own Claude subscription" in privacy or "your own Claude account" in privacy
    assert "no credential" in privacy.lower()


def test_the_release_says_which_combinations_are_fully_tested():
    """Two host/backend combinations are fully tested; the rest are beta.

    A beta reader picks a backend on this claim, so it states the maturity per COMBINATION rather
    than per component -- "supported" and "fully tested" are different assertions, and collapsing
    them invites someone to put the least-worn path on their most important repository without
    being told. Pinned because it is exactly the sort of claim that stays in a README one release
    after it stopped being true."""
    readme = prose((ROOT / "README.md").read_text(encoding="utf-8"))

    assert "Two combinations are fully tested" in readme
    # Both tested combinations named, with their backend -- not just "Codex works".
    assert "| Claude Code | codex | fully tested |" in readme
    assert "| Codex | Codex child session | fully tested |" in readme
    # And the beta ones named as beta, with the ask.
    assert "beta -- feedback wanted" in readme or "beta — feedback wanted" in readme
    assert '"Supported" and "fully tested" are different claims' in readme
