#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerAgent on [acceptRisk]|off` implementation.

on:  verify cwd is a git repo -> verify ./reviewerAgent.md exists -> create the
     per-project active flag. Validation is local, so this is INSTANT: backend briefing
     is deferred to first use (ensure_session / SessionStart), because briefing makes
     the reviewer read the codebase and took minutes on a large repo. Any validation
     failure is printed as an ERROR and the flag is NOT set.
off: remove the per-project active flag (backend sessions are left intact).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


_DISCLAIMER = Path(__file__).resolve().parent / "DISCLAIMER.md"


def _acceptance_required():
    print(
        "Before enabling reviewerAgent you must accept the risk as described in "
        "DISCLAIMER.md by invoking:\n/reviewerAgent on acceptRisk",
        file=sys.stderr,
    )
    return 1


def _on(proj, accept_risk=False):
    # Key + brief at the CANONICAL project root so `on` works from the main checkout, a
    # subdirectory, or a worktree (where reviewerAgent.md may not be checked out), and the
    # flag is keyed identically to how the hooks resolve it.
    root = rl.canonical_project_key(proj)
    if not rl.is_git_repo(root):
        print(
            "reviewerAgent: ERROR - {} is not a git repository. Aborting "
            "(the reviewer needs a git repo).".format(root),
            file=sys.stderr,
        )
        return 1
    brief = Path(root) / "reviewerAgent.md"
    if not brief.exists():
        print(
            "reviewerAgent: ERROR - brief not found at {}. Create reviewerAgent.md "
            "first.".format(brief),
            file=sys.stderr,
        )
        return 1
    try:
        disclaimer_hash = rl.disclaimer_sha256(_DISCLAIMER)
        if not rl.risk_accepted(root, disclaimer_hash):
            if not accept_risk:
                return _acceptance_required()
            rl.record_risk_acceptance(
                root, disclaimer_hash, "/reviewerAgent on acceptRisk"
            )
    except OSError as error:
        print(
            "reviewerAgent: ERROR - could not record risk acceptance: {}".format(error),
            file=sys.stderr,
        )
        return 1
    # Validation is LOCAL ONLY -- no backend call here. Briefing the reviewer makes it read
    # the codebase, which took minutes on a large repo and made `/reviewerAgent on` feel
    # hung. The brief is instead LAZY: the first hook that needs the reviewer calls
    # ensure_session() (which briefs and persists the session id), and SessionStart briefs
    # for projects that are already active. So enabling is instant and the cost is paid
    # once, on the first gated action.
    rl.set_active(root)
    rl.log("[on] root={} (flag set; reviewer briefed lazily on first use)".format(root))
    print(
        "reviewerAgent: ON for {} - the reviewer is briefed on first use, so the first "
        "commit it gates will take a moment.".format(root)
    )
    _warn_about_an_old_host()
    _state_the_risk(root)
    return 0


# Shown when gating starts so the operator sees the principal risks at the point of enablement.
# LAST, so it is the final thing on screen rather than scrolled away by the notices above.
_RISK_NOTICE = (
    "reviewerAgent: USE AT YOUR OWN RISK. An AI reviewer will read staged changes and send "
    "review context to the configured provider. On the Codex backend it may run commands in "
    "the repository. Its output may be wrong and it may consume provider quota. Keep "
    "independent backups and review resulting code. Full terms: {}"
)


def _state_the_risk(root):
    """Print the risk notice after explicit acceptance and successful enablement."""
    try:
        print(_RISK_NOTICE.format(_DISCLAIMER))
        rl.log("[on] risk notice shown root={}".format(root))
    except Exception:  # a notice must never be the reason enabling fails
        pass


def _warn_about_an_old_host():
    """Say so when the host is older than the versions this release was validated against.

    Here rather than at install time because this is the moment the reviewer starts gating
    anything, and because a plugin install has no script of its own to hook. Never fatal, and
    never fatal to enabling: the floors are the oldest versions EXERCISED, not the oldest that
    can work, so an older host is a caveat rather than a refusal."""
    try:
        import host_versions

        warning = host_versions.claude_code_warning()
    except Exception:  # a version notice is never worth breaking the command over
        return
    if warning:
        print("reviewerAgent: WARNING - " + warning, file=sys.stderr)
        rl.log("[on] host version warning: {}".format(warning))


def main(argv):
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    proj = rl.project_dir()
    if action == "on" and len(argv) in (2, 3):
        if len(argv) == 3 and argv[2] != "acceptRisk":
            print("usage: toggle.py on [acceptRisk]|off", file=sys.stderr)
            return 2
        return _on(proj, accept_risk=len(argv) == 3)
    if action == "off" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        rl.clear_active(root)
        print("reviewerAgent: OFF for {}".format(root))
        return 0
    print("usage: toggle.py on [acceptRisk]|off", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
