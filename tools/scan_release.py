#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Scan tracked content, or a built export, for anything that must not be published.

Publishing is irreversible in a way that a bad release is not: an address is scraped, a token is
used, a home path names a person. So this runs over the repository on every pull request AND
over the generated export before it is published as the release archive, and it does not trust
`.gitignore` to be the boundary -- ignore rules are one line away from including a directory
nobody meant to publish, and `docs/plans/` versus `docs/internal/` differ by exactly that.

What it refuses:

  home paths        `/Users/<name>` and `/home/<name>` name a real person's account
  email addresses   any address outside the allowlisted, deliberately non-routable domains
  credentials       provider token shapes and private-key headers
  build artifacts   caches and runtime state that must never be in a source tree
  private commits   a 40-hex identifier that resolves to a commit in the private history
  private terms     an optional LOCAL list (see PRIVATE_TERMS_FILE) that never ships itself

What it permits, deliberately: `/Users/x/...`, which the test suite uses as a synthetic project
path in dozens of places, and `test@example.com`, which is RFC 2606 reserved. Both are documented
fixtures, and a scanner tuned to reject them would be turned off within a week.

  python3 tools/scan_release.py                 # the tracked files of this repository
  python3 tools/scan_release.py --path <dir>    # an exported tree
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import release_export  # noqa: E402

# Synthetic account names, and nothing else. `x` is the fixture account the suite uses in
# dozens of paths; `runner` is GitHub Actions' own home, which appears in workflow files and
# CI output. Plausible-looking words like `user` and `home` are NOT allowed: accounts with
# those literal names exist on real machines, so allowlisting them would let a real home path
# through a scanner whose stated job is to reject exactly that. (No example path is spelled out
# in this file -- the scanner reads its own source, and would flag it.)
ALLOWED_HOME_NAMES = {"x", "runner"}

# Reserved or deliberately non-routable domains. `example.com` is RFC 2606; the GitHub noreply
# domain cannot receive mail from strangers and is the published identity for this project.
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "users.noreply.github.com"}

# An optional local file, one term per line, listing strings that must never be published --
# a real username, a business domain, a private repository name. It is deliberately NOT tracked
# and NOT exported: a deny list of private terms is itself private, and shipping one would
# publish exactly what it exists to withhold.
PRIVATE_TERMS_FILE = ROOT / "tools" / "private_terms.txt"

PATTERNS = (
    ("home path", re.compile(r"/(?:Users|home)/([A-Za-z0-9._-]+)")),
    ("email address", re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")),
    # `sk-ant-` has its own row below; excluding it here keeps one leak from being
    # reported twice under two names.
    ("openai key", re.compile(r"\bsk-(?!ant-)[A-Za-z0-9_-]{16,}")),
    ("github token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}")),
)

# Paths that must not exist in a source tree at all, checked by name rather than by content.
FORBIDDEN_PATHS = (
    "__pycache__", ".pytest_cache", "node_modules", ".DS_Store",
    ".claude/settings.local.json",
)

# Runtime state directories. Matched as whole path components so a source file named
# `reviewer_state.py` or a `tests/` directory is untouched.
FORBIDDEN_COMPONENTS = ("state", "logs", "reviews")

_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2", ".zip"}


def _redact(value):
    """A non-reversible description of a match: its length and nothing else."""
    return "(redacted, {} chars)".format(len(value))


def private_terms():
    """Terms from the local, untracked deny list; empty when it does not exist."""
    try:
        lines = PRIVATE_TERMS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def repository_files(path):
    """Every file to scan: tracked files in a repository, or every file in an exported tree.

    In repository mode the export's own exclusion list applies. Internal release documents are
    tracked deliberately and never published, so flagging their contents would report findings
    about text that cannot reach the public tree -- and a scanner with standing known findings
    is a scanner people stop reading. The export is scanned separately, where those files are
    absent by construction rather than by rule.
    """
    path = Path(path)
    git = subprocess.run(
        ["git", "-C", str(path), "ls-files"], capture_output=True, text=True
    )
    if git.returncode == 0 and git.stdout.strip():
        return [
            path / name
            for name in git.stdout.split()
            if not release_export.is_excluded(name)
        ]
    return [item for item in path.rglob("*") if item.is_file()]


def _known_commit(path, sha):
    """True when `sha` names a commit in this repository -- i.e. private history leaking out."""
    result = subprocess.run(
        ["git", "-C", str(path), "cat-file", "-t", sha], capture_output=True, text=True
    )
    return result.returncode == 0 and result.stdout.strip() == "commit"


def scan(path=ROOT, check_commits=True):
    """Every finding as (file, line, kind, evidence). Empty means publishable."""
    path = Path(path)
    findings = []
    terms = private_terms()
    for item in repository_files(path):
        relative = item.relative_to(path).as_posix()
        parts = set(Path(relative).parts)
        for forbidden in FORBIDDEN_PATHS:
            if forbidden in relative:
                findings.append((relative, 0, "forbidden path", forbidden))
        for component in FORBIDDEN_COMPONENTS:
            if component in parts:
                findings.append((relative, 0, "runtime state", component))
        if item.suffix.lower() in _BINARY_SUFFIXES or "node_modules/" in relative:
            continue
        try:
            text = item.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            for kind, pattern in PATTERNS:
                for match in pattern.finditer(line):
                    if kind == "home path" and match.group(1) in ALLOWED_HOME_NAMES:
                        continue
                    if kind == "email address" and match.group(1) in ALLOWED_EMAIL_DOMAINS:
                        continue
                    # The MATCH IS NEVER ECHOED. This output goes to CI logs, which are
                    # retained and often public; printing the credential or home path a
                    # scanner just found would publish it in the course of reporting it.
                    # The file and line are enough to act on.
                    findings.append((relative, line_number, kind, _redact(match.group(0))))
            for term in terms:
                if term.lower() in line.lower():
                    findings.append((relative, line_number, "private term", _redact(term)))
            if check_commits:
                for match in _SHA.finditer(line):
                    if _known_commit(ROOT, match.group(0)):
                        findings.append(
                            (relative, line_number, "private commit id",
                             _redact(match.group(0)))
                        )
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--path", default=str(ROOT), help="repository or exported tree to scan")
    parser.add_argument(
        "--no-commit-check", action="store_true",
        help="skip resolving 40-hex strings against this repository's history",
    )
    args = parser.parse_args()

    findings = scan(args.path, check_commits=not args.no_commit_check)
    if not findings:
        print("scan_release: clean ({})".format(args.path))
        if not private_terms():
            print(
                "scan_release: note - no local private-term list at {}; generic patterns "
                "only".format(PRIVATE_TERMS_FILE)
            )
        return 0
    for relative, line, kind, evidence in findings:
        print("{}:{}: {}: {}".format(relative, line, kind, evidence), file=sys.stderr)
    print("\nscan_release: {} finding(s)".format(len(findings)), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
