# Plan: stopContinue prompt-hardening + a configurable pre-enactment delay

Two related changes to make `stopContinue` less likely to permit a premature stop, and to give
the operator a window to intervene on any of its decisions.

## Problem

Observed (2026-09-01, `research_out` session, log line 22:38:40): the agent ended its turn with
"Still pending from HANDOVER: ... **Say the word and I'll do the brief next.**" and the supervisor
returned `STOP_OK`, permitting the stop even though the pending work was in scope and needed no new
approval. It is not a code bug: the codex supervisor genuinely ran and output `STOP_OK`
(`is_stop_ok` parsed it correctly; it was not a fail-open). The judgment is also **noisy** —
near-identical messages got opposite verdicts (research_out 22:29 "…commit, then update the science
brief." → continue; 22:38 "…say the word and I'll do the brief next." → STOP_OK). The trigger for a
wrong stop is a **polite go-ahead request** ("say the word", "shall I", "let me know", "I'll do X
next") for work the agent just named: the model reads it as a genuine operator-decision requirement
(`STOP_SUPERVISE_PROMPT` "WHEN STOPPING IS PERMITTED" case 2) instead of authorized remaining work.

## Part A — prompt-hardening (root-cause fix)

Operator policy for the supervisor:

1. If the agent's final message indicates **pending / remaining in-scope work** and does **not**
   claim it requires operator approval, it must be told to **continue** — automatically. A polite
   go-ahead request ("say the word", "shall I", "let me know", "I'll do X next", "ready when you
   are") for named in-scope work is remaining work, **not** an approval requirement.
2. If the message **does** claim it requires approval, the supervisor must **verify** that claim:
   permit the stop only if the action genuinely needs operator authorization (an AUTHORITY LIMITS
   action, or information only the operator can supply) **and** that authorization was not already
   granted in the operator's messages or an approved plan. If it was already granted, or the action
   does not actually need approval, state the controlling decision and continue.

Implementation: extend `STOP_SUPERVISE_PROMPT` (reviewer_prompts.py) — strengthen
"AGENT-IDENTIFIED REMAINING WORK" to name the go-ahead phrasings and classify them as remaining
work, and sharpen "WHEN STOPPING IS PERMITTED" / the authorization check so a *claimed* approval
requirement is verified against the authority limits and the already-granted test before a stop is
permitted. The `STOP_OK` sentinel and `is_stop_ok` parsing are unchanged.

**Native Codex parity (required).** reviewerAgent.md requires the two stop-supervisor prompts to
stay aligned: the SAME hardening must be applied to `STOP_BRIEF` in
`plugins/reviewer-agent-codex/reviewer_codex/prompts.py` (structurally parallel — same
"AGENT-IDENTIFIED REMAINING WORK" / "WHEN STOPPING IS PERMITTED" sections). A parity test pins that
both prompts carry the new policy phrases so a change to one cannot silently skip the other.

Testing is necessarily partial: unit tests pin that the new guidance text is present in the prompt
(content assertions); the model's judgment itself is not unit-testable. This is why Part B exists.

## Part B — configurable pre-enactment delay (deterministic safety valve)

A user-configurable pause before `stopContinue` **enacts** a decision, applied to **both** a
continue directive and a stop approval, so the operator has a window to intervene (Escape) even
when the model's call is wrong — including the fail-open timeout case is out of scope (that path
has no verdict; the delay covers the two verdict branches).

- `stopcontinue_delay_secs()` (reviewer_stopcontinue.py, re-exported via reviewer_lib): reads
  `REVIEWER_STOPCONTINUE_DELAY_SECS` env or `stopContinueDelaySecs` in `config.json`, default `0`
  (no delay), floored at `0`, capped at `300`. Same env/config pattern as `max_nudges` and
  `stopContinueEffort` (no new slash command).
- `apply_stopcontinue_delay(decision)` (reviewer_stopcontinue.py): when the delay is `> 0`, writes
  a best-effort heads-up to stderr (`[stopContinue] applying <decision> in Ns -- interrupt now to
  override.`) so an operator can Escape during the window, then sleeps via a `_delay_sleep` seam
  (so tests fake it). `decision` is `"continue"` or `"stop-approval"`.
- `stop_hook.py`: after the supervisor verdict and logging (~line 236), and only when a verdict was
  produced (`keep_going` or a `STOP_OK`), call `apply_stopcontinue_delay(...)` before emitting the
  decision. Both branches are covered by the single call. The nudge bump and watchdog arm keep
  their current positions inside their branches (after the delay).

**Delay must not overrun host cancellation.** The Stop hook is cancelled by the host after the
`hooks/hooks.json` Stop timeout (currently 330 s), and the reviewer call itself can take up to
`STOP_TIMEOUT` (300 s), so a 300 s delay after a slow reviewer would be cut off before the decision
is emitted — and a cut-off Stop hook fails open (the stop proceeds). Two mitigations, both required:
(a) raise the `hooks/hooks.json` Stop timeout to fit `STOP_TIMEOUT` + delay cap + margin
(300 + 300 + 30 = 630 s); and (b) `apply_stopcontinue_delay` caps the actual sleep against the
REMAINING hook budget — `min(configured, HOOK_BUDGET_S - elapsed - margin)` where `HOOK_BUDGET_S`
mirrors the hooks.json Stop timeout and `elapsed` is measured from hook entry — so the decision is
always emitted before cancellation even if the reviewer was slow or the timeouts drift. A test pins
`HOOK_BUDGET_S` equal to the hooks.json Stop timeout so the mirror cannot go stale.

Watchdog ticks that reach the supervisor are delayed too (they are stopContinue decisions); the
added latency is bounded by the configured cap and the default `0` leaves current behavior
unchanged.

## Files

- `reviewer_prompts.py` — harden `STOP_SUPERVISE_PROMPT`.
- `plugins/reviewer-agent-codex/reviewer_codex/prompts.py` — harden `STOP_BRIEF` identically
  (native Codex parity).
- `reviewer_stopcontinue.py` — `stopcontinue_delay_secs()`, `apply_stopcontinue_delay()`,
  `_delay_sleep` seam, `HOOK_BUDGET_S`.
- `reviewer_lib.py` — re-export the new names; then regenerate `tests/test_facade_contract.py`
  (`python3 tools/gen_facade_contract.py`) so the checked-in facade contract matches the new
  callers (enforced by `tests/test_facade_generator.py`).
- `stop_hook.py` — record hook-entry time; call the delay (budget-capped) before the verdict
  branches.
- `hooks/hooks.json` — raise the Stop timeout to 630 s so a full delay after a slow reviewer fits.
- NEW `tests/test_stopcontinue_delay.py` — config read (default/env/config/cap/floor), apply
  (delay 0 = no sleep; delay > 0 = heads-up + sleep N; budget cap shortens it), stop_hook
  integration (both the continue and STOP_OK branches sleep before emitting, via the seam), and a
  pin that `HOOK_BUDGET_S` equals the hooks.json Stop timeout.
- Prompt-content assertions for Part A on BOTH prompts (Claude Code + native Codex parity), in a
  new or existing stop-prompt test.
- `README.md` / relevant docs — document `stopContinueDelaySecs` / `REVIEWER_STOPCONTINUE_DELAY_SECS`
  and the raised Stop-hook timeout.

## TDD sequence (operator policy: plan -> commit -> red -> commit -> green -> commit)

1. This plan -> commit.
2. Red: `tests/test_stopcontinue_delay.py` + the prompt-content/parity assertions -> commit red.
3. Green: implement Part A (both prompts) + Part B (delay + hooks.json timeout) + reviewer_lib
   re-exports + regenerate the facade contract + docs -> commit green.
4. Version bump + dist regen (batched with the reroute release). Release/push is the operator's call.
