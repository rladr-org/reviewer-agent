#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerHistory [clear]` implementation.

Retained reviews are plaintext local data. Every completed audit's full verdict is kept, and
verdicts quote the staged diff they were judging, so a project's history directory accumulates
a readable record of the code under review as well as of the reviews themselves. The retention
cap bounds how much of it survives (the newest 100 per project by default, oldest pruned first),
but a cap is not a purge: until this command existed, removing that record meant deleting paths
by hand or uninstalling the plugin.

No argument reports what is retained for the current project and where. `clear` deletes it --
the retained history AND the saved latest rulings, since both hold the same kind of content and
an operator asking to purge review history does not mean "leave the newest one behind".

Scoped to one canonical project root, resolved the same way every other per-project control
resolves it, so a linked worktree purges its main checkout's history and no other repository's
history is touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_audit  # noqa: E402
import reviewer_lib as rl  # noqa: E402


def _show(proj):
    root = rl.canonical_project_key(proj)
    retained = rl.review_history(proj)
    directory = rl.review_history_dir(proj)
    print("reviewerHistory: project {}".format(root))
    if retained:
        print("reviewerHistory: {} retained review(s) in {}".format(len(retained), directory))
        print("reviewerHistory: oldest {}".format(retained[0].name))
        print("reviewerHistory: newest {}".format(retained[-1].name))
    else:
        print("reviewerHistory: nothing retained yet in {}".format(directory))
    print(
        "reviewerHistory: retention keeps the newest {} review(s) per project and prunes "
        "oldest-first; set REVIEWER_REVIEW_HISTORY_KEEP or `reviewHistoryKeep` in config.json "
        "to change it.".format(rl.review_history_keep())
    )
    print(
        "reviewerHistory: /reviewerHistory clear deletes this project's retained reviews, its "
        "saved latest rulings, its audit log, and the stored prompt parts no other project "
        "uses. Other repositories are untouched."
    )
    return 0


def _clear(proj):
    root = rl.canonical_project_key(proj)
    removed = rl.clear_review_history(proj) + reviewer_audit.purge_project_audit_data(proj)
    if not removed:
        print(
            "reviewerHistory: nothing to clear for {} -- no retained reviews, saved ruling or "
            "audit log.".format(root)
        )
        return 0
    rl.log("[history] cleared proj={} files={}".format(root, len(removed)))
    print(
        "reviewerHistory: cleared {} file(s) for {} (retained reviews, saved rulings, the "
        "audit log, and stored prompt parts no other project uses). The next review starts a "
        "fresh record; reviewer sessions are unaffected.".format(len(removed), root)
    )
    return 0


def main(argv):
    args = [a.strip() for a in argv[1:] if a.strip()]
    if len(args) > 1 or (args and args[0].lower() != "clear"):
        print(
            "reviewerHistory: ERROR - expected no argument (show) or `clear`, got: "
            "{!r}".format(args),
            file=sys.stderr,
        )
        return 2
    proj = rl.project_dir()
    return _clear(proj) if args else _show(proj)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
