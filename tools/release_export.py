#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Build the public release tree: what ships, what never ships, and a reproducible archive.

The public repository is created from an EXPORT, not from this checkout: it gets one root
commit containing exactly the files listed here. Two properties matter more than convenience.

EXPLICIT EXCLUSION, NOT IGNORE RULES. `.gitignore` decides what Git tracks, which is a different
question from what a release publishes -- `docs/plans/` ships and `docs/internal/` does not, and
those two differ by one line of configuration. Internal release planning is tracked deliberately
(the commit gate reviews a plan before the code implementing it) and must still never be
published. So the exclusion list is stated here, by name, and the export verifies that none of
its entries survived.

REPRODUCIBLE ARCHIVE. `--archive` writes a tarball with sorted members, zeroed timestamps and
no owner metadata, so two exports of the same tree are byte-identical. That is what makes "the
archive is clean" a checkable claim rather than an impression.

  python3 tools/release_export.py --target /tmp/reviewer-agent-export
  python3 tools/release_export.py --target /tmp/export --archive /tmp/export.tar.gz
  python3 tools/release_export.py --list
"""
import argparse
import fnmatch
import gzip
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Internal documents. Tracked on purpose -- the reviewer audits a plan before the code that
# implements it -- and published never.
EXCLUDED_PATHS = (
    "PUBLIC_BETA_RELEASE_PLAN.md",
    "PUBLIC_BETA_RELEASE_PLAN_REVIEW.md",
)

EXCLUDED_PATTERNS = (
    "docs/internal/*",
    "tools/private_terms.txt",
    ".claude/settings.local.json",
)

# Files whose absence would make the export broken rather than merely incomplete.
REQUIRED_PATHS = (
    ".gitignore",
    "LICENSE",
    "DISCLAIMER.md",
    "README.md",
    "CHANGELOG.md",
    "pytest.ini",
    "requirements-dev.txt",
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    "plugins/reviewer-agent-codex/LICENSE",
    "plugins/reviewer-agent-codex/.codex-plugin/plugin.json",
)


def is_excluded(relative):
    if relative in EXCLUDED_PATHS:
        return True
    return any(fnmatch.fnmatch(relative, pattern) for pattern in EXCLUDED_PATTERNS)


def tracked_files(root=ROOT):
    return subprocess.run(
        ["git", "-C", str(root), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()


def included_files(root=ROOT):
    """Every tracked file that ships, sorted so an export is deterministic."""
    return sorted(name for name in tracked_files(root) if not is_excluded(name))


def excluded_files(root=ROOT):
    return sorted(name for name in tracked_files(root) if is_excluded(name))


def export(target, root=ROOT):
    """Copy the shipping files into `target`. Returns the list of relative paths written."""
    target = Path(target)
    if target.exists() and any(target.iterdir()):
        raise SystemExit("release_export: {} exists and is not empty".format(target))
    names = included_files(root)
    for name in names:
        source = Path(root) / name
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return names


def write_archive(target, archive_path, root=ROOT):
    """A deterministic .tar.gz of the exported tree.

    Sorted members, fixed mtime, zeroed uid/gid and no gzip timestamp: two runs over the same
    content produce the same bytes, which is what lets a release be verified rather than
    described.
    """
    target = Path(target)
    names = sorted(
        item.relative_to(target).as_posix() for item in target.rglob("*") if item.is_file()
    )
    with open(str(archive_path), "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for name in names:
                    info = archive.gettarinfo(str(target / name), arcname=name)
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with open(str(target / name), "rb") as handle:
                        archive.addfile(info, handle)
    return archive_path


def verify(target, root=ROOT):
    """Problems with an exported tree; empty means it is publishable."""
    target = Path(target)
    problems = []
    for name in EXCLUDED_PATHS:
        if (target / name).exists():
            problems.append("excluded file present: {}".format(name))
    for pattern in EXCLUDED_PATTERNS:
        for item in target.glob(pattern):
            problems.append("excluded pattern present: {}".format(
                item.relative_to(target).as_posix()))
    for name in REQUIRED_PATHS:
        if not (target / name).is_file():
            problems.append("required file missing: {}".format(name))
    if (target / ".git").exists():
        problems.append("the export must not carry a .git directory")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", help="directory to write the export into")
    parser.add_argument("--archive", help="also write a reproducible .tar.gz here")
    parser.add_argument("--list", action="store_true", help="list what ships and what does not")
    args = parser.parse_args()

    if args.list or not args.target:
        included = included_files()
        print("ships: {} file(s)".format(len(included)))
        print("excluded by name:")
        for name in excluded_files():
            print("  " + name)
        for pattern in EXCLUDED_PATTERNS:
            print("  {} (pattern)".format(pattern))
        return 0

    names = export(args.target)
    problems = verify(args.target)
    if problems:
        for problem in problems:
            print("release_export: " + problem, file=sys.stderr)
        return 1
    print("release_export: wrote {} file(s) to {}".format(len(names), args.target))
    if args.archive:
        write_archive(args.target, args.archive)
        print("release_export: archive {}".format(args.archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
