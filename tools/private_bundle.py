#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Create and verify an offline archive of a repository's full history.

The history -- the reasoning, the failures, the measurements recorded in commit messages --
survives in this bundle only if the bundle actually works. `git bundle create` succeeding proves
nothing about that: the useful question is whether a repository can be RESTORED from it and
worked in.

So `verify` does all of:

  * the SHA-256 digest, compared against the value recorded when the bundle was created --
    this is what detects a copy on offline media that has rotted or been substituted, and it
    is the check the release plan requires immediately before anything is deleted;
  * `git bundle verify`, which checks the bundle's own integrity and prerequisites;
  * a real restore into a throwaway checkout;
  * a ref-by-ref and HEAD comparison, against the source repository when it is available and
    against the recorded manifest when it is not (an offline copy is often checked on a
    machine that no longer has the source, which is rather the point);
  * both canonical Python suites, run from the restored copy.

`create` writes a `.json` manifest beside the bundle carrying that digest, every ref, HEAD and
the commit count. Keep the two together on each medium: without the manifest there is nothing
to verify a copy against, and `verify` says so rather than passing.

The restore checkout is deleted afterwards by default. It is a verification step, not a third
copy of the history, and leaving full-history clones lying around is exactly what the release
plan is trying to end.

  python3 tools/private_bundle.py create --target ~/reviewer-agent-private-bundle
  python3 tools/private_bundle.py verify --bundle <path>            # full: restore + suites
  python3 tools/private_bundle.py verify --bundle <path> --quick    # integrity + refs only
  python3 tools/private_bundle.py verify --bundle <path> --expected-sha256 <hex>
"""
import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=check
    )


def digest(path):
    sha = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def refs(repo):
    """Every ref and the object it points at, as a sorted, comparable mapping."""
    listing = _git(repo, "show-ref").stdout.splitlines()
    return {line.split(" ", 1)[1]: line.split(" ", 1)[0] for line in listing if " " in line}


def create(target, source=ROOT):
    target = Path(target).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle = target / "reviewer-agent-private-{}.bundle".format(stamp)
    # EVERY ref by name, not `--all`. `--all` covers heads, tags and remotes, and this
    # repository also carries refs in a custom namespace (`refs/codex/...`, written by the
    # Codex CLI). Verification caught them missing: an archive that silently drops a namespace
    # is discovered only when someone needs it, which by then is after the source is gone.
    named = sorted(refs(source))
    subprocess.run(
        ["git", "-C", str(source), "bundle", "create", str(bundle), "HEAD"] + named,
        check=True, capture_output=True, text=True,
    )
    manifest = {
        "bundle": bundle.name,
        "created": stamp,
        "sha256": digest(bundle),
        "bytes": bundle.stat().st_size,
        "source_head": _git(source, "rev-parse", "HEAD").stdout.strip(),
        "source_refs": refs(source),
        "commits": int(_git(source, "rev-list", "--all", "--count").stdout.strip()),
    }
    (target / (bundle.stem + ".json")).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return bundle, manifest


def manifest_for(bundle):
    """The manifest written beside a bundle by `create`, or None."""
    candidate = Path(bundle).with_suffix(".json")
    try:
        return json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


UNSET = object()


def resolve_source(bundle_manifest, source=UNSET):
    """Which repository, if any, this bundle should be compared against.

    `None` means "compare against the recorded manifest instead", and that is the normal case
    once the private repository is gone -- which is exactly when an offline copy gets checked.

    The automatic choice is not "whatever repository this script lives in". The checkout this
    tool runs from may be a perfectly valid Git repository that shares no commit with the
    bundle: comparing a good bundle against it would fail every ref check and read as a
    corrupt archive. So a candidate source
    is used only if it actually contains the commit the manifest recorded as HEAD.
    """
    if source is None:
        return None
    if source is not UNSET:
        return Path(source).expanduser().resolve()
    head = (bundle_manifest or {}).get("source_head")
    if not head:
        return ROOT if (ROOT / ".git").exists() else None
    present = subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "-e", head],
        capture_output=True, text=True,
    )
    return ROOT if present.returncode == 0 else None


def verify(bundle, source=UNSET, quick=False, keep=False, expected=None):
    """Everything that has to be true before the private history is deleted anywhere else."""
    bundle = Path(bundle).expanduser().resolve()
    actual = digest(bundle)
    manifest = manifest_for(bundle)
    source = resolve_source(manifest, source)
    report = {"bundle": str(bundle), "sha256": actual, "checks": []}

    def record(description, passed, detail=""):
        report["checks"].append(
            {"check": description, "passed": bool(passed), "detail": str(detail)[:400]}
        )
        return passed

    # FIRST, and required. Everything below can pass on a bundle that is not the archive you
    # think it is; only this check can tell you that it IS. A missing expectation is a FAILURE,
    # not a pass with a note: "there was nothing to compare against" is precisely the state in
    # which someone deletes the last other copy.
    recorded = expected or (manifest or {}).get("sha256")
    if not recorded:
        record("digest compared against a recorded value", False,
               "no manifest beside the bundle and no --expected-sha256; nothing to verify "
               "this copy against")
    else:
        record("digest matches the recorded value", recorded == actual,
               "recorded {}, found {}".format(recorded[:16], actual[:16]))

    integrity = subprocess.run(
        ["git", "bundle", "verify", str(bundle)], capture_output=True, text=True
    )
    record("git bundle verify", integrity.returncode == 0,
           (integrity.stderr or integrity.stdout).strip()[-300:])

    restore = Path(tempfile.mkdtemp(prefix="bundle-restore-"))
    try:
        # TWO restores, because they answer different questions. A MIRROR restores every ref
        # exactly as named, which is what "the archive is complete" means; a plain clone brings
        # across only heads and tags, so comparing refs against one would report a complete
        # archive as incomplete and an incomplete one as fine.
        mirror = restore / "mirror.git"
        mirrored = subprocess.run(
            ["git", "clone", "--mirror", "-q", str(bundle), str(mirror)],
            capture_output=True, text=True,
        )
        record("restores as a mirror", mirrored.returncode == 0, mirrored.stderr.strip()[-300:])
        if mirror.is_dir():
            # Compare against the live source when this machine still has it, and against the
            # manifest otherwise. An offline copy is usually checked somewhere the source no
            # longer exists -- if that could only ever be checked against the source, the
            # check would be unavailable exactly when it matters.
            source_available = bool(source) and (Path(source) / ".git").exists()
            if source_available:
                expected_refs = refs(source)
                expected_head = _git(source, "rev-parse", "HEAD").stdout.strip()
                expected_commits = _git(source, "rev-list", "--all", "--count").stdout.strip()
                against = "the source"
            else:
                expected_refs = (manifest or {}).get("source_refs") or {}
                expected_head = (manifest or {}).get("source_head") or ""
                expected_commits = str((manifest or {}).get("commits") or "")
                against = "the recorded manifest"
            restored_refs = refs(mirror)
            missing = {name: sha for name, sha in expected_refs.items()
                       if restored_refs.get(name) != sha}
            record("every ref is restored by name and object, against {}".format(against),
                   bool(expected_refs) and not missing,
                   ", ".join(sorted(missing))[:300] or "nothing to compare against")
            record(
                "HEAD matches {}".format(against),
                bool(expected_head)
                and _git(mirror, "rev-parse", "HEAD").stdout.strip() == expected_head,
            )
            record(
                "commit count matches {}".format(against),
                bool(expected_commits)
                and _git(mirror, "rev-list", "--all", "--count").stdout.strip()
                == expected_commits,
            )

        # ...and a WORKING clone, because an archive nobody can work from is not an archive.
        cloned = subprocess.run(
            ["git", "clone", "-q", str(mirror if mirror.is_dir() else bundle),
             str(restore / "repo")],
            capture_output=True, text=True,
        )
        record("restores into a working repository", cloned.returncode == 0,
               cloned.stderr.strip()[-300:])
        repo = restore / "repo"
        if repo.is_dir():
            if not quick:
                # The suites, from the RESTORED copy: a bundle you cannot work from is not an
                # archive of a working repository, it is a file that once contained one.
                environment = {"PYTHONDONTWRITEBYTECODE": "1", "PATH": _path()}
                root_suite = subprocess.run(
                    [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
                    cwd=str(repo), capture_output=True, text=True, env=environment,
                    timeout=5400,
                )
                record("the root suite passes from the restored copy",
                       root_suite.returncode == 0, root_suite.stdout[-300:])
                nested = subprocess.run(
                    [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
                    cwd=str(repo / "plugins" / "reviewer-agent-codex"),
                    capture_output=True, text=True, env=environment, timeout=5400,
                )
                record("the nested suite passes from the restored copy",
                       nested.returncode == 0, nested.stdout[-300:])
    finally:
        if keep:
            report["restore_kept_at"] = str(restore)
        else:
            # Deleted deliberately: this was a test, not an archive. The release plan's whole
            # point is that no full-history checkout survives except the offline bundles.
            shutil.rmtree(restore, ignore_errors=True)
            report["restore_deleted"] = True
    report["compared_against"] = str(source) if source else "the recorded manifest"
    report["passed"] = all(item["passed"] for item in report["checks"])
    return report


def _path():
    import os
    return os.environ.get("PATH", "/usr/bin:/bin")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    maker = sub.add_parser("create", help="write a bundle and its manifest")
    maker.add_argument("--target", required=True, help="directory to write the bundle into")

    checker = sub.add_parser("verify", help="prove a bundle can be restored and worked from")
    checker.add_argument("--bundle", required=True)
    checker.add_argument("--quick", action="store_true", help="skip the test suites")
    checker.add_argument(
        "--expected-sha256",
        help="the digest this copy must have; defaults to the manifest beside the bundle",
    )
    checker.add_argument(
        "--source",
        help="the private repository to compare against; defaults to this checkout when it "
             "still contains the recorded HEAD, and to the manifest otherwise",
    )
    checker.add_argument(
        "--offline", action="store_true",
        help="compare against the recorded manifest only, ignoring any local repository",
    )
    checker.add_argument("--keep", action="store_true", help="keep the restore checkout")
    checker.add_argument("--report", help="write the verification report here as JSON")

    args = parser.parse_args()
    if args.command == "create":
        bundle, manifest = create(args.target)
        print("bundle:  {}".format(bundle))
        print("sha256:  {}".format(manifest["sha256"]))
        print("commits: {}".format(manifest["commits"]))
        print("manifest: {}".format(Path(args.target).expanduser() / (bundle.stem + ".json")))
        return 0

    if args.offline and args.source:
        parser.error("--offline and --source are mutually exclusive")
    source = None if args.offline else (args.source if args.source else UNSET)
    report = verify(args.bundle, source=source, quick=args.quick, keep=args.keep,
                    expected=args.expected_sha256)
    for item in report["checks"]:
        print("{} {}{}".format("PASS" if item["passed"] else "FAIL", item["check"],
                               "" if item["passed"] else "  <- " + item["detail"]))
    print("\ncompared against: {}".format(report["compared_against"]))
    print("sha256: {}".format(report["sha256"]))
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print("report: {}".format(args.report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
