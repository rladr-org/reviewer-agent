#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerSession [rotate]` implementation.

No argument reports this Claude session's reviewer: which backend it uses, whether a session has
been established yet, and its id. `rotate` retires it, so the next gated action briefs a fresh
reviewer.

Rotation is MANUAL only. Automatic rotation (on every approved commit, or on a turn ceiling) was
measured and rejected: the first commit review in a session costs a median 93k tokens against a
flat ~40k steady state from turn 3 out past turn 41, so any automatic trigger would pay a cold
start every time it fired, and it would also move the fail-closed brief-failure exposure from
once per Claude session to once per commit. See REVIEW_COST_REDUCTION_PLAN.md A1.3.

What rotation is genuinely for: the brief is sent ONCE per session, so a session established
before a change to the standing instructions keeps running on the old ones until it is retired.
Rotating is how a new brief reaches a live session without restarting Claude Code. It can also
replace a reviewer whose accumulated context is no longer useful.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _session_id():
    """This Claude session's id, or None.

    CLAUDE_CODE_SESSION_ID is what Claude Code exports (verified 2026-07-30: its value equals the
    transcript stem, which is the same key the hooks derive via claude_session_key). The legacy
    name is accepted as a fallback because harness variable names have changed before -- and
    rotating the WRONG session would retire a reviewer mid-review, so guessing is not an option:
    with neither variable set this reports an error instead of picking a session.
    """
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


def _show(proj, sid):
    backend = rl.reviewer_backend(proj)
    sessions = {
        "codex": {
            rl.ROLE_REVIEWER: rl.read_session_id(proj, sid, role=rl.ROLE_REVIEWER),
            rl.ROLE_STOP: rl.read_session_id(proj, sid, role=rl.ROLE_STOP),
        },
        "pi": {
            rl.ROLE_REVIEWER: rl.read_pi_session(proj, sid, role=rl.ROLE_REVIEWER),
            rl.ROLE_STOP: rl.read_pi_session(proj, sid, role=rl.ROLE_STOP),
        },
        "claude": {
            rl.ROLE_REVIEWER: rl.read_claude_session(proj, sid, role=rl.ROLE_REVIEWER),
            rl.ROLE_STOP: rl.read_claude_session(proj, sid, role=rl.ROLE_STOP),
        },
    }
    reviewer = sessions[backend][rl.ROLE_REVIEWER]
    supervisor = sessions[backend][rl.ROLE_STOP]
    # Name the project every line refers to. Reviewer state is per (project, session) now, so a
    # report that named only the session would describe state an operator standing in a
    # different repository does not have -- and rotation acts on exactly this root.
    print("reviewerSession: project {}".format(rl.canonical_project_key(proj)))
    if reviewer:
        print("reviewerSession: {} reviewer {} for this Claude session".format(
            backend, reviewer))
    else:
        print(
            "reviewerSession: no reviewer session yet for this Claude session "
            "(backend {}); one is briefed lazily on the first gated action".format(backend)
        )
    if supervisor:
        print("reviewerSession: {} stopContinue supervisor {} for this Claude session".format(
            backend, supervisor))
    else:
        print(
            "reviewerSession: no {} stopContinue supervisor session yet; one is briefed "
            "lazily at the first supervised turn end".format(backend)
        )

    # Switching backends mid-session leaves the previous backend's role slots on disk. Report
    # them when present so an operator can see exactly what `/reviewerSession rotate` will clear.
    for other_backend in sorted(name for name in sessions if name != backend):
        other_reviewer = sessions[other_backend][rl.ROLE_REVIEWER]
        other_supervisor = sessions[other_backend][rl.ROLE_STOP]
        if other_reviewer:
            print("reviewerSession: also holds a {} reviewer session {}".format(
                other_backend, other_reviewer))
        if other_supervisor:
            print("reviewerSession: also holds a {} stopContinue supervisor session {}".format(
                other_backend, other_supervisor))
    print("reviewerSession: /reviewerSession rotate retires all of these role sessions FOR "
          "THIS PROJECT; each next gated action briefs a fresh one with the current standing "
          "brief. Reviewer sessions in other repositories are untouched.")
    return 0


def _rotate(proj, sid):
    root = rl.canonical_project_key(proj)
    cleared = rl.rotate_session(proj, sid)
    if not cleared:
        print(
            "reviewerSession: nothing to rotate -- no reviewer session was established for "
            "this Claude session in {}. The next gated action will brief a fresh one "
            "anyway.".format(root)
        )
        return 0
    rl.log("[session] rotated proj={} sid={} cleared={}".format(root, sid, len(cleared)))
    print(
        "reviewerSession: reviewer session for {} RETIRED ({} state file(s) cleared). The "
        "next gated action briefs a fresh reviewer with the current standing brief. Expect "
        "that one review to cost more than usual -- a first review in a session is the "
        "expensive one.".format(root, len(cleared))
    )
    return 0


def main(argv):
    args = [a.strip() for a in argv[1:] if a.strip()]
    if len(args) > 1 or (args and args[0].lower() != "rotate"):
        print(
            "reviewerSession: ERROR - expected no argument (show) or `rotate`, got: "
            "{!r}".format(args),
            file=sys.stderr,
        )
        return 2

    sid = _session_id()
    if sid is None:
        print(
            "reviewerSession: ERROR - cannot identify this Claude session "
            "(CLAUDE_CODE_SESSION_ID is not set), so there is no session to report or "
            "retire. Nothing was changed.",
            file=sys.stderr,
        )
        return 2
    proj = rl.project_dir()
    return _rotate(proj, sid) if args else _show(proj, sid)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
