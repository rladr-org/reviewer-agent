# SPDX-License-Identifier: MPL-2.0
"""Regression guard for the auto-mode classifier block on the reviewer slash-commands.

The six reviewer commands (/reviewerAgent, /reviewerModel, /reviewerHistory, /reviewerSession,
/stopContinue, /stopContinueWatchdog) each run a `!` shell pre-exec. In Claude Code `auto` mode a
pre-exec with no matching allow rule is refused by the classifier before it runs -- the failure the
operator sees as "/reviewerAgent on is refused by the classifier". AGENT_INSTALL.md's fix is ONE
scoped settings.json allow rule, `Bash(python3 ".../reviewer_cmd.py":*)`, and that single rule only
works because every reviewer command dispatches through the single script reviewer_cmd.py.

If a new reviewer command shipped a pre-exec that ran a DIFFERENT script -- or passed a subcommand
reviewer_cmd.py does not dispatch -- that command would again be classifier-blocked in auto mode
with no allow rule covering it, and the one documented rule would silently not help. These tests
fail on that drift. They read only repo files, so they are environment-independent.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"

# The pre-exec line, e.g.:  !`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" agent "$ARGUMENTS"`
_PREEXEC = re.compile(
    r'!`python3\s+"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z_][\w.]*\.py)"\s+(\w+)\b'
)


def _skill_preexecs():
    """{skill_dir_name: (script_basename, subcommand)} for every SKILL.md with a python pre-exec."""
    out = {}
    for skill_md in sorted(SKILLS.glob("*/SKILL.md")):
        m = _PREEXEC.search(skill_md.read_text(encoding="utf-8"))
        if m:
            out[skill_md.parent.name] = (m.group(1), m.group(2))
    return out


def _dispatcher_subcommands():
    sys.path.insert(0, str(ROOT))
    import reviewer_cmd  # stdlib-only import; the toggle modules load lazily inside main()
    return set(reviewer_cmd._SUBCOMMANDS)


def test_every_reviewer_skill_dispatches_through_reviewer_cmd():
    preexecs = _skill_preexecs()
    assert preexecs, "no reviewer skill pre-exec matched -- regex or SKILL.md layout drift"
    for skill, (script, _sub) in sorted(preexecs.items()):
        assert script == "reviewer_cmd.py", (
            "{} runs {} in its `!` pre-exec; every reviewer command must dispatch through the "
            "single reviewer_cmd.py so ONE settings.json allow rule "
            "(Bash(python3 \".../reviewer_cmd.py\":*)) unblocks all of them under auto mode".format(
                skill, script))


def test_every_skill_subcommand_is_a_real_dispatcher_subcommand():
    subs = _dispatcher_subcommands()
    for skill, (_script, sub) in sorted(_skill_preexecs().items()):
        assert sub in subs, (
            "{} passes subcommand '{}' that reviewer_cmd.py does not dispatch (known: {}). An "
            "undispatched subcommand fails after the classifier already allowed the script.".format(
                skill, sub, sorted(subs)))


def test_agent_install_documents_a_version_independent_allow_rule():
    doc = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    # The documented allow rule must WILDCARD the version segment (`*/reviewer_cmd.py`) so it
    # survives `claude plugin update` -- the installed path is version-stamped and a pinned rule
    # goes stale on every bump (durability fix, 2026-09-09). A doc that regressed to a concrete
    # pinned path -- e.g. `.../reviewer-agent/1.2.3/reviewer_cmd.py` -- would fail here.
    assert re.search(r'Bash\(python3 [^\n)]*\*/reviewer_cmd\.py', doc), (
        "AGENT_INSTALL.md must document a VERSION-INDEPENDENT allow rule "
        "(Bash(python3 \".../*/reviewer_cmd.py\":*)); a version-pinned path goes stale on the next "
        "plugin update")


def test_agent_install_documents_the_on_classifier_exception():
    doc = (ROOT / "AGENT_INSTALL.md").read_text(encoding="utf-8")
    # The permission allow rule does NOT clear the state-changing `on`; the docs must keep telling
    # operators how (autoMode.allow, or turning auto mode off), or `/reviewerAgent on` stays broken
    # under auto mode with no documented remedy.
    assert "autoMode.allow" in doc or "auto mode off" in doc, (
        "AGENT_INSTALL.md must document how to allow the state-changing `on` under auto mode "
        "(autoMode.allow exception, or turning auto mode off) -- the permission rule alone does not")
