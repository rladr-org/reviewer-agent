# SPDX-License-Identifier: MPL-2.0
"""Local-link and private-reporting structure. Not documentation content.

This file used to pin the wording of `SECURITY.md`, `PRIVACY.md` and `CHANGELOG.md`: that they
named a reporting channel, described both execution boundaries, listed every storage location,
and so on. Those tests were removed on the same principle that emptied
`test_repository_distribution.py`: **prose should not need a passing test to be edited**, and a
policy document that can only change when a test changes with it is a document nobody improves.

Removing them has a cost worth naming. Those documents make claims, and nothing now checks that a
claim stays true when the code beneath it changes. The answer to that is not a test -- it is to
stop making claims, which is why `SECURITY.md` and `PRIVACY.md` were cut back to what can be said
without asserting behaviour a reader could rely on.

What is left here is machine-readable structure that fails invisibly: local Markdown destinations
and the private vulnerability-reporting route.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INLINE_LINK = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REFERENCE_LINK = re.compile(r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE)


def _local_link_targets(text):
    for match in INLINE_LINK.finditer(text):
        yield match.group(1).strip().split(None, 1)[0].strip("<>")
    for match in REFERENCE_LINK.finditer(text):
        yield match.group(1).strip().strip("<>")


def test_every_local_markdown_link_resolves():
    """Local links fail silently on GitHub, so verify destinations rather than prose wording."""
    markdown = sorted(
        path for path in ROOT.rglob("*.md")
        if (
            "node_modules" not in path.parts
            and ".git" not in path.parts
            and (ROOT / "docs" / "internal") not in path.parents
        )
    )
    assert markdown

    broken = []
    for path in markdown:
        text = path.read_text(encoding="utf-8")
        for target in _local_link_targets(text):
            if not target or target.startswith("#") or "://" in target or target.startswith("mailto:"):
                continue
            destination = target.split("#", 1)[0]
            if destination and not (path.parent / destination).resolve().exists():
                broken.append((path.relative_to(ROOT).as_posix(), target))

    assert broken == []


def test_the_template_config_routes_security_reports_privately():
    """A vulnerability filed as a public issue is a published exploit for a tool that runs in
    someone's shell. This checks the routing exists, not what any document says about it."""
    text = (ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml").read_text(encoding="utf-8")

    assert "blank_issues_enabled: true" in text
    assert "security/advisories/new" in text
