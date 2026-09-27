#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""`/stopContinueWatchdog on [interval]|off|status` implementation.

Toggles a per-PROJECT flag that arms a CronCreate loop. The cron re-invokes an idle session
so the EXISTING Stop hook (stopContinue) can recover an unsupervised fail-open strand -- the
watchdog itself adds NO supervision. The model does the actual CronCreate/CronList/CronDelete
per the command's instructions; this script only owns the flag + interval and reports state.

`on` REQUIRES stopContinue to already be active: the tick relies on the Stop hook, which
returns immediately when the stopContinue flag is off, so a watchdog without it would fire
tick turns that are never supervised. Requiring the existing flag also keeps its
risk-acceptance gate intact. `status` reports `stopContinueWatchdog: ON|OFF ...` for the operator;
the tick checks the flag files itself.

See docs/plans/stopcontinue_watchdog.md.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


def _err(msg):
    print("stopContinueWatchdog: ERROR - " + msg, file=sys.stderr)
    return 1


def _on(proj, interval):
    root = rl.canonical_project_key(proj)
    if not rl.is_git_repo(root):
        return _err("{} is not a git repository. Aborting.".format(root))
    if not rl.is_stopcontinue_active(root):
        return _err(
            "stopContinue is not active for {}. Enable it first with `/stopContinue on "
            "acceptRisk` -- the watchdog relies on the Stop hook, which is inert while "
            "stopContinue is off.".format(root)
        )
    if not rl.is_valid_watchdog_interval(interval):
        return _err(
            "interval must be an integer {}-{} minutes (got {!r}).".format(
                rl.WATCHDOG_MIN_INTERVAL, rl.WATCHDOG_MAX_INTERVAL, interval
            )
        )
    # The tick prompt embeds a SESSION-scoped teardown_ready marker path, so arming needs this
    # Claude session's id. A slash command receives only argv, but Claude Code exports the id in
    # the environment. Fail CLOSED if it is absent rather than arming an unscoped or wrong-session
    # cron -- guessing could tear down another session's watchdog.
    sid = rl.claude_session_id_from_env()
    if not sid:
        return _err(
            "cannot determine this Claude session's id (CLAUDE_CODE_SESSION_ID is not set), so "
            "the watchdog cron cannot be armed with its session-scoped marker. Not arming."
        )
    rl.set_stopcontinue_watchdog_active(root, interval)
    print("stopContinueWatchdog: ON for {} (interval {} min).".format(root, interval))
    print(rl.watchdog_arm_instruction(root, interval, sid))
    return 0


def main(argv):
    action = argv[1].strip().lower() if len(argv) > 1 else ""
    proj = rl.project_dir()
    if action == "on":
        interval = rl.WATCHDOG_DEFAULT_INTERVAL
        if len(argv) >= 3 and str(argv[2]).strip():
            try:
                interval = int(str(argv[2]).strip())
            except ValueError:
                return _err(
                    "interval must be an integer {}-{} minutes (got {!r}).".format(
                        rl.WATCHDOG_MIN_INTERVAL, rl.WATCHDOG_MAX_INTERVAL, argv[2]
                    )
                )
        return _on(proj, interval)
    if action == "off" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        rl.clear_stopcontinue_watchdog_active(root)
        print("stopContinueWatchdog: OFF for {}.".format(root))
        print(rl.WATCHDOG_CANCEL_INSTRUCTION)
        return 0
    if action == "status" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        # Labelled like `on`/`off`, because the skill relays one `stopContinueWatchdog: ON|OFF`
        # line verbatim (#20). EFFECTIVE state, not the bare flag: armed while /stopContinue is
        # off is reported OFF (dormant), since the Stop hook the tick relies on is inert.
        if rl.is_stopcontinue_watchdog_effective(root):
            print("stopContinueWatchdog: ON for {} (interval {} min).".format(
                root, rl.watchdog_interval(root)))
        elif rl.is_stopcontinue_watchdog_active(root):
            print("stopContinueWatchdog: OFF for {} (armed, but dormant because /stopContinue "
                  "is off).".format(root))
        else:
            print("stopContinueWatchdog: OFF for {}.".format(root))
        return 0
    print(
        "usage: stopcontinue_watchdog_toggle.py on [interval]|off|status", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
