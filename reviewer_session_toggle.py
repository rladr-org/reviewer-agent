#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/reviewerSession [rotate | limit <N|off|clear>]` implementation.

No argument reports this Claude session's reviewer: which backend it uses, whether a session has
been established yet, and its id. `rotate` retires it, so the next gated action briefs a fresh
reviewer.

By default, automatic rotation happens after three completed first-pass audits. Corrective retry
audits do not count. The limit command overrides the config-file default; zero/off disables it.
"""
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
    return rl.claude_session_id_from_env()


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
    limit, source = rl.reviewer_session_audit_limit_detail()
    if limit:
        print("reviewerSession: automatic rotation after {} completed non-retry audit(s) "
              "(from {})".format(limit, source))
    else:
        print("reviewerSession: automatic rotation disabled (from {})".format(source))
    if reviewer:
        print("reviewerSession: {} reviewer {} for this Claude session".format(
            backend, reviewer))
        audit_id = reviewer if backend == "codex" else backend + ":" + reviewer
        print("reviewerSession: current reviewer has completed {} counted audit(s)".format(
            rl.reviewer_session_completed_audits(proj, sid, audit_id)
        ))
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
    print("reviewerSession: /reviewerSession rotate retires these role sessions for THIS "
          "project in THIS Claude session; each next gated action briefs a fresh one with the "
          "current standing brief. Other Claude sessions and other repositories keep their own "
          "reviewers.")
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


def _limit(value):
    token = str(value or "").strip().lower()
    if token == "clear":
        rl.clear_reviewer_session_audit_limit()
    elif token == "off":
        rl.set_reviewer_session_audit_limit(0)
    else:
        try:
            parsed = int(token)
        except ValueError:
            parsed = -1
        if not 1 <= parsed <= 1000 or str(parsed) != token:
            print("reviewerSession: ERROR - limit expects 1..1000, off, or clear",
                  file=sys.stderr)
            return 2
        rl.set_reviewer_session_audit_limit(parsed)
    limit, source = rl.reviewer_session_audit_limit_detail()
    if limit:
        print("reviewerSession: automatic rotation after {} completed non-retry audit(s) "
              "(from {})".format(limit, source))
    else:
        print("reviewerSession: automatic rotation disabled (from {})".format(source))
    return 0


def main(argv):
    args = [a.strip() for a in argv[1:] if a.strip()]
    valid = (not args or args == ["rotate"] or
             (len(args) == 2 and args[0].lower() == "limit"))
    if not valid:
        print(
            "reviewerSession: ERROR - expected no argument, `rotate`, or "
            "`limit <N|off|clear>`, got: "
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
    if not args:
        return _show(proj, sid)
    if args[0].lower() == "limit":
        return _limit(args[1])
    return _rotate(proj, sid)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
