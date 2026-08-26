---
description: Toggle the stopContinueWatchdog for this project. Arms a CronCreate loop that re-invokes an idle session so the existing Stop hook can recover an unsupervised fail-open strand. Requires stopContinue active. Usage: /stopContinueWatchdog on [interval]|off|status
allowed-tools: Bash(python3:*), CronList, CronCreate, CronDelete
disable-model-invocation: true
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/stopcontinue_watchdog_toggle.py" $ARGUMENTS`

The block above is the live output of the toggle script (it ran before you saw this).

First, relay the state to the user in one short line, quoting it verbatim from the output:
`stopContinueWatchdog: ON ...`, `stopContinueWatchdog: OFF ...`, or
`stopContinueWatchdog: ERROR ...` (surface any error text exactly). On an ERROR (e.g. stopContinue
is not active), do nothing further.

Then, if and only if the output contains a `[stopContinueWatchdog]` instruction block, FOLLOW IT NOW
using the Cron tools — this is the mid-session arming/cancellation, done in this active turn, not
left to a future SessionStart:

- an **ARM** instruction: call `CronList`, then reconcile the marker-tagged tasks (whose prompt
  contains the marker) to **exactly one** at the schedule the instruction names — keep one already
  at that schedule and `CronDelete` every other marker-tagged task; if none is at that schedule,
  `CronDelete` all marker-tagged tasks and `CronCreate` one with the tick prompt **verbatim** from
  the instruction.
- a **CANCEL** instruction: call `CronList` and `CronDelete` every task whose prompt contains the
  marker.

Take no other action, and never invoke the toggle script yourself.
