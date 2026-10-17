# Plan: a 60-second reading delay before the stop review, on both hosts

## Problem

The stop supervisor can push the agent back to work while the operator is still reading the
agent's reply. The operator asked (2026-10-17) for a default 60-second window on both hosts,
and chose to wait BEFORE the review rather than after it, "because it prevents wasted tokens":
a reply typed during the window skips the review entirely.

Today the hosts differ:

| | Claude Code | Codex |
| --- | --- | --- |
| Setting | `stopContinueDelaySecs` / `REVIEWER_STOPCONTINUE_DELAY_SECS`, 0..300 | same key and env var, 0..300 |
| Default | 0 | 15 |
| When | after the review, before either verdict is enacted | before the review |
| Command | none | `$reviewer-agent-codex:stop-continue delay [0..300\|status\|clear]` |

## Change

### Claude Code

- **Timing.** `stop_hook.py` waits before the review instead of before enacting its verdict.
  The wait comes after every early return that needs no review (stopContinue off, tool-only stop,
  background wait, paused or teardown tick, a reply already queued, nudge cap) and after the
  declined-directives check, which either allows the stop at once or falls through to the review.
  It sits just before the supervisor prompt is built. The post-review pause is removed, so an
  approved stop ends as soon as the review approves it.
- **A reply cancels the wait.** The wait polls the existing `operator_reply_watch` every half
  second. A reply ends it, and the stop is allowed with no supervisor call
  (`operator replied during the reading delay -> supervisor call skipped`).
- **Budget.** The wait is capped at `HOOK_BUDGET_S - elapsed - STOP_TIMEOUT - DELAY_MARGIN_S`
  (630 - 300 - 30 = 300 s at hook entry), so the full review still fits before Claude Code
  cancels the hook. The hook timeout stays 630 s. A first stop on the codex backend also briefs a
  new supervisor session; a slow brief can still run past the budget, and the hook then fails
  open (the stop is allowed), as stop supervision always does.
- **Default 60.** `stopcontinue_delay_detail()` returns `(seconds, source)` with the precedence
  the other Claude Code tunables use: env `REVIEWER_STOPCONTINUE_DELAY_SECS` > state file
  `state/stopcontinue_delay` (written by the command) > `config.json stopContinueDelaySecs` >
  default 60. Each value is clamped to 0..300, and an unusable one is skipped. 0 turns the wait
  off.
- **Command.** `/reviewer-agent:stopContinue delay [<0..300>|status|clear]`, handled by
  `stopcontinue_toggle.py` (routed through `reviewer_cmd.py`): a number saves the state file,
  `clear` removes it, and `delay` / `delay status` prints
  `stopContinue: delay <n> seconds (from <source>; applies to every project)`. The setting is
  user-wide, like the commit timeout. The skill's description, argument hint and relay text name
  the new form.
- **Heads-up.** Before waiting, the hook writes
  `[stopContinue] supervisor review in <n>s -- reply to skip it.` to stderr and logs
  `[stop] key=... reading delay <n>s before the supervisor review`.

### Codex

- Default `DEFAULT_STOPCONTINUE_DELAY_SECS` 15 -> 60. Timing, command and cancellation are
  unchanged (it already waits before the review and a submitted message cancels it).

### Tests

- Root `tests/conftest.py` sets `REVIEWER_STOPCONTINUE_DELAY_SECS=0` for every test, as the Codex
  conftest already does, so the new default does not make stop-hook tests sleep. Tests of the
  delay set it themselves.
- `tests/test_stopcontinue_delay.py` is rewritten for the new behavior: default 60; env, state
  file and config precedence; clamping; the wait happens before the review is called and not
  after its verdict; no wait on the early-return paths; the budget cap; a reply during the wait
  skips the review. The prompt-hardening tests in that file are unchanged.
- `tests/test_stop_operator_reply.py`: the pre-enactment delay test becomes "a reply during the
  reading delay skips the supervisor".
- A command test for `delay`, `delay 30`, `delay status`, `delay clear` and invalid values.
- Codex: `test_stop_reading_delay.py` and `test_stop_delay_command.py` expect 60.

### Documentation

README (both stopContinue sections and the env-var table), the Codex README and skill, the
Claude Code stopContinue skill, and CHANGELOG `[Unreleased]`.

## TDD sequence

1. This plan -> commit.
2. Red: the tests above -> commit.
3. Green: code, facade contract regenerated, docs -> commit.
