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
risk-acceptance gate intact. `status` prints ACTIVE/INACTIVE for the tick's self-cancel check.

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
    rl.set_stopcontinue_watchdog_active(root, interval)
    print("stopContinueWatchdog: ON for {} (interval {} min).".format(root, interval))
    print(rl.watchdog_arm_instruction(root, interval))
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
        # EFFECTIVE state, not the bare flag: if stopContinue was turned off after the watchdog
        # was armed, the tick must read INACTIVE so it self-cancels its now-unsupervised cron.
        print("ACTIVE" if rl.is_stopcontinue_watchdog_effective(root) else "INACTIVE")
        return 0
    print(
        "usage: stopcontinue_watchdog_toggle.py on [interval]|off|status", file=sys.stderr
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
