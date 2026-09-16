# Watchdog tick — direct a per-path check, not a batched `ls`

## Operator TDD-commit policy (governs this work)

The operator's development policy for this repository is:

> **plan → commit → red test → commit → code passing tests → commit**

A commit whose staged change is an intentionally-failing ("red") test, authored as the red step
of this policy, is an EXPECTED, first-class step — not an accident. The reviewer must not reject a
commit *solely* because a newly-added test fails against the staged code when that commit is the
red step of this workflow (its message / the operator context marks it so). The green step that
follows — the implementation that makes the test pass — is reviewed exactly as strictly as before.

## Why

A `stopContinueWatchdog` recovery tick (built by `watchdog_tick_prompt` in `reviewer_prompts.py`)
names three filesystem paths and tells the model to read them: the session-scoped `teardown_ready`
marker, and the two flag files `stopcontinue_watchdog.active` and `stopcontinue.active`. The tick
then decides: tear the cron down if `teardown_ready` exists OR either flag is missing; do nothing
if the paths cannot be read (fail-safe); otherwise keep working.

Observed in a live session, a tick checked all three paths with a single batched
`ls ready wd sc`. Because `teardown_ready` is armed **only** on a genuine STOP_OK verdict, it is
ABSENT in every healthy, still-working session — so `ls ready wd sc` exits non-zero on the healthy
case. That surfaces a scary `Error: Exit code 1` in the transcript, and, worse, the batched exit
code conflates three outcomes the tick must keep distinct:

- `teardown_ready` absent (healthy — keep working),
- a flag file missing (should tear down),
- the filesystem genuinely unreadable (fail-safe — do NOT delete).

A model reading the batched exit-1 as "cannot read the files" would take the fail-safe branch and
NOT tear down in the flag-missing case — leaking the recovery cron. In the observed transcript the
per-line `ls` output still let the model reach the correct "keep working" branch, so the concrete
harm there was noise; the latent correctness risk is the reason to fix it.

The design intent recorded in `docs/plans/watchdog_tick_visibility.md §2` was already per-file
`Read`-tool calls, never a batched shell check — the prompt simply did not say so.

## Fix

Reword `watchdog_tick_prompt` so it:

1. Directs a **per-path** check (Read each path individually, one path per call) and explicitly
   warns off a single batched `ls`.
2. States that the `teardown_ready` marker is normally ABSENT, and that a path that is merely
   missing is NOT a read failure — so absence is never mistaken for unreadability.

The existing structural invariants are preserved (exactly one `CronDelete`, gated by an
`if … either … missing … CronDelete` clause; the `if … unfinished work … resume` rescue branch; a
quoted `ok` as the `otherwise` of the resume branch; no shell-out to a Python script; the
`stopContinueWatchdog tick` detection prefix; `len(tick) < 700` with short paths).

## Files

- `reviewer_prompts.py` — `watchdog_tick_prompt` reworded (green).
- `tests/test_watchdog_auto_shutoff.py` — new red test
  `test_tick_prompt_directs_per_path_check_not_a_batched_ls`; all existing tick assertions in it
  and in `tests/test_stopcontinue_watchdog_toggle.py` continue to pass unchanged.
- `docs/plans/stopcontinue_watchdog.md` — the "The tick prompt" example is refreshed to the
  current three-path, per-file form (it had drifted to the old two-flag description).

## Out of scope

- Version bump / dist regen. Per the operator's decision this change ships as prompt + tests + docs
  only; without a version bump an installed directory-source copy will not pick it up (see the
  CHANGELOG preamble), so live sessions keep the old tick until a later release bumps the version.
