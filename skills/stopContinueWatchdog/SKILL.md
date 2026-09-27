---
name: stopContinueWatchdog
description: Toggle the stopContinueWatchdog for this project. Arms a CronCreate loop that re-invokes an idle session so the existing Stop hook can recover an unsupervised fail-open strand. Requires stopContinue active. Usage: /stopContinueWatchdog on [interval]|off|status
argument-hint: on [interval] | off | status
disable-model-invocation: true
allowed-tools: Bash(python3:*), CronList, CronCreate, CronDelete
---

!`python3 "${CLAUDE_PLUGIN_ROOT}/reviewer_cmd.py" watchdog "$ARGUMENTS"`

The block above is the live output of the toggle script (it ran before you saw this).

First, relay the state to the user in one short line, quoting it verbatim from the output:
`stopContinueWatchdog: ON ...`, `stopContinueWatchdog: OFF ...`, or
`stopContinueWatchdog: ERROR ...` (surface any error text exactly). On an ERROR (e.g. stopContinue
is not active), do nothing further.

Then, if and only if the output contains a `[stopContinueWatchdog]` instruction block, FOLLOW IT NOW
using the Cron tools — this is the mid-session arming/cancellation, done in this active turn, not
left to a future SessionStart:

- an **ARM** instruction: call `CronList`, then reconcile the marker-tagged tasks (whose prompt
  contains the marker) to **exactly one** at the schedule the instruction names AND carrying the
  instruction's exact tick prompt — keep a single task only if its schedule matches AND its prompt
  is identical to the instruction's tick prompt, and `CronDelete` every other marker-tagged task
  (including any at the right schedule but with a different or outdated prompt); if none matches,
  `CronDelete` all marker-tagged tasks and `CronCreate` one with the tick prompt **verbatim** from
  the instruction. Matching by prompt is what lets a changed tick replace a stale cron.
- a **CANCEL** instruction: call `CronList` and `CronDelete` every task whose prompt contains the
  marker.

The cron lives only in this Claude session: it dies with the process and fires only while the
session is idle. After a restart, SessionStart re-arms it.

Take no other action, and never invoke the toggle script yourself.
