# Codex commit audit: raise N2 default and keep retrying while budget remains

## Why

Live-log observations from `~/.claude/reviewer-agent/logs/reviewer.log` on a large project
(a large reviewed project (`research_out/`), ~180 staged files / ~60 k staged lines) show the current defaults
sit too close to the noise floor and the single-retry cap gives up on transient hangs before
budget is exhausted.

- Healthy codex commit audits regularly produce `max_gap_s` of **300–370 s** (measured entries at
  06:38:19 `max_gap=328`, 07:24:54 `max_gap=315`, 08:49:27 `max_gap=372`). The current
  `DEFAULT_IDLE_TIMEOUT = 600` leaves only ~40 % headroom above healthy silence, so a mild codex
  slow-down starts tripping N2 on real reviews rather than genuine hangs.
- The current design retries an idle kill **once** and only once. Log entry 08:10:39 shows the
  single retry itself also went idle almost immediately (`max_gap=11.874` on the retry;
  `idle_retried=True`, exit=124). Under today's rule the audit then fails closed and the commit is
  blocked, even though the total budget (2400 s configured) still had over an hour left. The
  human's judgement in the moment ("that was probably transient, try again") should be automated,
  because the total-budget cap already bounds worst-case wall time.

Adjacent finding out of scope here: token accounting (`tokens=None`) is broken because the parser
does not recognise this codex-cli version's `--json` usage event. Different investigation; not
part of this change.

## Change 1: DEFAULT_IDLE_TIMEOUT 600 → 900

Raise the N2 default from 600 s to 900 s (15 min).

- Restores ~150 % headroom over the observed 300–370 s healthy gaps (900 / 372 ≈ 2.4×) instead of
  the current ~40 % (600 / 372 ≈ 1.6×).
- Floor stays at 60 s (`IDLE_TIMEOUT_FLOOR`) — operators tuning a smaller repo can still choose a
  tighter value.
- The env / state / config precedence chain and the `/reviewer-agent:reviewerModel idleTimeout`
  slash command do not change. Only the built-in fallback flips.
- **Interaction with `DEFAULT_COMMIT_TIMEOUT = 900`:** on stock defaults, an N2 kill at 900 s
  leaves zero budget for a retry. This is not a regression in practice: a repo whose audits fit
  inside 900 s never hits N2 in the first place. The operators actually hitting N2 today are
  running long audits and have already raised `commit_review_timeout` (2400 s observed in the
  log); for them the retry loop below applies. Any small-repo operator who does want the retry
  cushion can raise `commit_review_timeout` explicitly — the plan does not change that shared
  default.

## Change 2: retry idle kills while budget remains

Replace the single-retry cap with a budget-bounded loop:

- The whole audit still shares **one absolute deadline** (`start + commit_review_timeout`), which
  is unchanged.
- On an N1/N2 idle kill, **retry as long as `remaining = deadline - now > 0`** AND the total
  number of idle-kill retries so far is below `MAX_IDLE_RETRIES = 8`. If either check fails, stop
  and return the last result (fail-closed).
- Total-cap timeouts are still never retried — the reason string carries `"review timed out
  after Ns"` and does not contain `"idle"`, matching the existing branch.
- `_run_streaming` already checks the total cap first and only then the idle windows, so a retry
  with `remaining` smaller than `idle_timeout` cannot mis-fire an idle kill inside the total-cap
  region — it will simply run to a total-cap kill and exit the loop cleanly.
- `MAX_IDLE_RETRIES = 8` bounds pathological configurations where a very small custom
  `idleTimeout` could otherwise let the loop spin many times against a broken codex. The 8 is a
  runaway backstop, not a policy floor — the budget check dominates in real operation.

**`idle_retried` becomes an integer count.** Current field is a boolean set to `True` on any
retry. Change to an int recording how many retry attempts ran (0 for no retry, ≥1 otherwise).
Because `bool(int)` matches the previous semantics, downstream truthiness checks continue to
work — but the field's **stored representation everywhere it is preserved must become the
integer**, not the boolean, or the count is lost to the log and history.

## Files that must change

### Code

- `reviewer_state.py` — `DEFAULT_IDLE_TIMEOUT` constant: 600 → 900.
- `reviewer_backends.py` — `run_codex_streaming_audit` retry loop: replace `while attempts < 2`
  and the single-branch idle retry with the budget+cap loop above; write `inv["idle_retried"] =
  <int count>` at the loop exit, not `True`.
- `reviewer_audit.py` — line 565: `"idle_retried": bool(invocation.get("idle_retried"))` must
  become `int(invocation.get("idle_retried") or 0)`, so the audit record preserves the count.
- `commit_hook.py` — audit-record log-line assembly (currently references the boolean field):
  emit the integer count in the `idle_retried=` position of the log so the value is legible for
  post-hoc calibration. This is a pure format change; the count value comes from the record via
  the reviewer_audit change above.

### Docs

- `docs/plans/codex_jsonl_streaming.md`:
  - §"Mechanism" — line 65 (N2 default 600 → 900), line 67 (rationale sentence rewritten to
    reflect the observed 300–370 s healthy gap and the ~2.4× headroom).
  - §"Mechanism" — retry bullet (currently line 74-77 "One automatic retry"): rewrite to
    "Retry any idle kill while budget remains, capped at MAX_IDLE_RETRIES."
  - §"Mechanism" — line 84 (config precedence: `> 600` → `> 900`).
  - §"Task 3 Implement `run_codex_streaming_audit`" — line 260-266 (the `max 2` attempts and the
    single-retry sentence): rewrite to describe the budget-bounded loop.
  - §"Task 5 Tests" — line 279-282 (the specific-scenario tests): expand to include
    (multi-retry-success, budget-exhausted-no-retry, MAX_IDLE_RETRIES-cap).
  - §"Notes" — line 188 (`DEFAULT_IDLE_TIMEOUT=600`): update to 900.
- `README.md`:
  - Line 621 command table: default `600 s (floor 60)` → `900 s (floor 60)`.
  - Line 826 env-var table: `REVIEWER_IDLE_TIMEOUT_SECS 600 (floor 60)` → `900 (floor 60)`.
- `CHANGELOG.md`: new "Changed" entry describing both changes and the `idle_retried` record
  format change.

### Not touched

- `IDLE_TIMEOUT_FLOOR = 60` — unchanged.
- N1 first-output default (30 s) — unchanged.
- `DEFAULT_COMMIT_TIMEOUT` (900 s) — unchanged.
- `parse_codex_turn_usage` — the `tokens=None` problem is a separate investigation.
- pi and claude backends — no changes.

## Tests (red before green, per TDD)

New tests (RED first, added to the existing `tests/test_commit_hook.py` and
`tests/test_reviewer_state.py` where they naturally belong):

- **N2 default is 900 not 600.**
  `reviewer_state.DEFAULT_IDLE_TIMEOUT == 900`; the `idleTimeoutSecs` resolver returns 900 when
  nothing else is set.
- **Two consecutive idle kills both retry, third attempt succeeds.** Fake `_run_streaming`
  returns `(TIMEOUT_EXIT, "", "idle timeout: ...")` twice and `(0, ..., "")` on the third call;
  `run_codex_streaming_audit` returns code 0 and the invocation's `idle_retried == 2`.
- **Retry stops when `remaining <= 0`.** Fake a first attempt that consumes the entire budget;
  assert no further attempt runs, code is `TIMEOUT_EXIT`, and `idle_retried == 0` because the
  first attempt itself was the terminal one (a total-cap kill, not an idle one).
- **`MAX_IDLE_RETRIES` cap fires under a pathologically small idle_timeout.** Fake
  `_run_streaming` returning idle-kill every call with `idle_timeout` small enough that the
  budget would allow >8 retries; assert the loop exits after 8 retries and returns fail-closed.
- **Total-cap timeout is never retried.** `_run_streaming` returns `(TIMEOUT_EXIT, "", "review
  timed out after Ns")` (no `"idle"` substring) — the loop exits after one attempt.
- **Audit-record preserves the integer count.** `reviewer_audit`'s stored `idle_retried` is `0`
  when no retry ran, `1` after one retry, `≥ 2` after multiple retries — verified via a small
  helper that reads the saved audit record.
- **Reviewer log line renders the integer.** `commit_hook`'s log line shows `idle_retried=3`
  after three retries, not `idle_retried=True`.

Existing tests updated:

- Tests currently asserting `idle_retried == True` or `is True` (identity) need to relax to
  `bool(...)` or an integer comparison. Search:
  `rg -n 'idle_retried' tests/` before implementing to enumerate the exact call sites.

## Non-goals

- Retrying a genuine total-cap timeout. That path stays as-is.
- Backoff between retries. Codex idle kills are not caused by rate limiting; adding a sleep
  would only extend wall time.
- Fixing `tokens=None`. Separate investigation.
- Any change to the pi or claude backends.
- Any change to `DEFAULT_COMMIT_TIMEOUT`. Operators of large repos already tune it upward.

## Open questions

- None currently — retry semantics match the observed operator behavior ("try again, the budget
  is still there"), the record-format change is enumerated everywhere it lives, and the numbers
  are calibrated from the live log.
