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


def _scanned(path, root):
    return (
        "node_modules" not in path.parts
        and ".git" not in path.parts
        and (root / "docs" / "internal") not in path.parents
    )


def _markdown_files(root):
    """Every Markdown file a checkout of `root` ships or is about to: tracked or new, never
    git-ignored. A git-ignored local snapshot (`logs/release-*`) copies only part of the tree, so
    its links point outside the copy and fail without saying anything about the published docs.
    Without a repository at `root` (an exported tree), every file is scanned."""
    import subprocess

    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard",
             "--", "*.md"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        top, listed = None, None
    if top is None or Path(top).resolve() != Path(root).resolve():
        paths = Path(root).rglob("*.md")
    else:
        paths = (Path(root) / name for name in set(listed.split("\0")) if name)
    return sorted(p for p in paths if p.is_file() and _scanned(p, Path(root)))


def _git_repo(tmp_path):
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    # The runner's own global excludes must not decide the result (one operator's ignores
    # `.gitignore` itself), so this repository points at an excludes file that does not exist.
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.excludesFile",
                    str(tmp_path / "no-global-excludes")], check=True)
    (tmp_path / ".gitignore").write_text("logs/\n")
    (tmp_path / "README.md").write_text("[ok](README.md)\n")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "OLD.md").write_text("[gone](../MISSING.md)\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "NEW.md").write_text("[new](../README.md)\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "README.md", ".gitignore"], check=True)
    return tmp_path


def test_markdown_scan_skips_git_ignored_files_but_keeps_new_ones(tmp_path):
    # A local snapshot under an ignored directory is not shipped, so its links are not checked;
    # a doc that is new and not yet staged is, so a broken link cannot wait for the commit.
    root = _git_repo(tmp_path)
    assert _markdown_files(root) == [root / "README.md", root / "docs" / "NEW.md"]


def test_markdown_scan_skips_a_tracked_file_deleted_from_disk(tmp_path):
    root = _git_repo(tmp_path)
    (root / "README.md").unlink()
    assert _markdown_files(root) == [root / "docs" / "NEW.md"]


def test_markdown_scan_without_git_checks_every_file(tmp_path):
    # An exported tree has no repository to ask; scanning everything keeps the check on.
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "OLD.md").write_text("x\n")
    (tmp_path / "README.md").write_text("x\n")
    assert _markdown_files(tmp_path) == [tmp_path / "README.md", tmp_path / "logs" / "OLD.md"]


def test_every_local_markdown_link_resolves():
    """Local links fail silently on GitHub, so verify destinations rather than prose wording."""
    markdown = _markdown_files(ROOT)
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
