# Watchdog tick — reduce the visible per-tick footprint

> **Composes with `docs/plans/watchdog_auto_shutoff.md`:** this plan makes each individual tick
> smaller; auto-shutoff makes there be *finitely many* of them (an idle session's cron tears itself
> down within a couple of idle intervals — the first idle tick past the interval arms `teardown_ready`
> and the next tick deletes the cron — instead of ticking indefinitely). Note that auto-shutoff
> further reshaped the tick into a fail-safe presence check that also reads a `teardown_ready`
> marker, so the tick is now somewhat larger than the minimal Read-based form described here — the
> "finitely many ticks" win is what dominates.

## Why

Each `stopContinueWatchdog` tick fires as a full Claude Code turn (per the platform ceiling
recorded in `docs/plans/stopcontinue_watchdog.md`: only a session-armed timer can wake an idle
interactive TUI, and today that's `CronCreate`). The turn is unavoidable. What's *visible* per
turn is not: today's tick surfaces four things to the operator:

1. A ~200-character English "tick prompt" injected as a user message.
2. A bash tool call rendering `python3 stopcontinue_watchdog_toggle.py status` → `ACTIVE` /
   `INACTIVE`.
3. The model's response ("Watchdog: ACTIVE. Done." or similar).
4. The Stop hook's own render (unchanged — that's the payload of a tick).

At the current 7-min interval that's ~200 turns per day of accumulated clutter, most of which
report "nothing changed." The operator's ask: keep the mechanism, cut the visible surface.

## Design principles the mechanism must preserve

1. **The tick still fires the Stop hook.** The whole point of the watchdog is that the Stop
   hook runs at the end of the tick's turn, and that hook is what actually supervises.
   Anything that skips the Stop hook defeats the watchdog.
2. **Self-cancellation on `INACTIVE`.** Today's tick checks the flag and self-cancels its own
   cron when the flag is off. This must survive the visibility reduction, otherwise turning
   the watchdog off in a different session leaves a stale cron ticking until the next tick
   fires (at most one interval away — 1–59 min, default 7 min) — the self-cancel branch
   runs on that tick and clears it.
3. **No regression on fail-open recovery.** The retry loop (per
   `docs/plans/codex_audit_retry_budget.md`) and Stop-hook supervision are unchanged; this is
   a UX change only.

## Shape

Three small changes, composable:

### 1. Shorter tick prompt

Today:

> `stopContinueWatchdog tick -- run \`python3 "/…/stopcontinue_watchdog_toggle.py" status\`. If it prints INACTIVE, delete THIS watchdog cron (CronDelete) and stop. If it prints ACTIVE: if you have unfinished work from before, resume it; otherwise reply briefly that you are done and stop.`

Proposed:

> `stopContinueWatchdog tick — check /…/state/<project>.stopcontinue_watchdog.active and .stopcontinue.active; if either is missing, CronDelete this cron and stop. Else: if you have unfinished work from before, resume it; otherwise reply \`ok\` and stop.`

Roughly 25–30% shorter. Reads as a directive, not a paragraph. Every character in the tick
prompt is re-injected on every fire; cutting ~60 characters per fire × 200 fires/day is
real accumulation.

The "resume unfinished work" branch is preserved verbatim from today's tick prompt. It's
load-bearing for recovery: `stop_hook.py:98-105` passes the tick turn's final text into the
supervisor decision, and a bare "ok" response from a genuinely-stranded session would let
the supervisor STOP-OK on that (interpreting "ok" as completion). The healthy-tick common
case still contracts to the two-character `ok` reply — but the branch that actually
rescues a strand stays intact.

### 2. Replace bash status probe with two `Read` tool calls

Today the tick shells out to `stopcontinue_watchdog_toggle.py status`. Under the visibility
change the tick uses `Read` on the two flag files instead:

- `Read` `<state_dir>/<project_key>.stopcontinue_watchdog.active` — presence = watchdog flag
  set.
- `Read` `<state_dir>/<project_key>.stopcontinue.active` — presence = stopContinue flag set.

Both must be present for the tick to consider the watchdog "effectively on." A missing file
short-circuits to `CronDelete this cron and stop` — matching today's `is_stopcontinue_watchdog_effective`
predicate exactly.

Two `Read` calls instead of one bash call render as slimmer tool cards (Claude Code's Read
rendering is more compact than a shell subprocess). Both files carry small text content
(neither is zero-byte, contrary to an earlier draft of this plan): `stopcontinue.active`
holds `"on\n"` (3 bytes, per `reviewer_state.py:947-950`), and `stopcontinue_watchdog.active`
holds the interval in minutes as its content (per `reviewer_state.py:982-1002`). Both Read
outputs are one short line each — total render is a handful of characters across two thin
tool cards.

The tick prompt names the exact filesystem paths, resolved by the toggle scaffolding at
arm-time. The prompt is a template with the resolved paths substituted in, not a computed
path at tick-time.

### 3. Single-token response on `ACTIVE`

The tick prompt tells the model to reply `ok` verbatim on the effective-ACTIVE-and-nothing-to-resume
path — no prose, no acknowledgement paragraph. Response shrinks to 2 characters of visible
assistant text on healthy ticks. On the effective-ACTIVE-with-unfinished-work path, the
model resumes as directed by the branch (that's a real recovery turn — not a "quiet tick"
case, and legitimately produces more visible output because it's doing actual work).

Combined effect per healthy tick (effective-ACTIVE with nothing to resume — the common case):

- Injected prompt: ~120 chars (down from ~200)
- Two Read tool calls: each a slim card with a single short line of content
  (`stopcontinue.active` → `on\n`; `stopcontinue_watchdog.active` → the interval integer, e.g.
  `7\n`)
- Assistant response: `ok`
- Stop hook rendering: unchanged — that's the actual work of the tick

## Files that must change

### Code

- `reviewer_prompts.py`:
  - `WATCHDOG_TICK_PROMPT` (currently the module-level string at `:238-244` that hard-codes
    the toggle-script path via `_WATCHDOG_TOGGLE_PATH`) becomes a **function**
    `watchdog_tick_prompt(proj) -> str` that:
    - Resolves the two flag-file paths (`stopcontinue_watchdog_flag_file(proj)`,
      `stopcontinue_flag_file(proj)`) via existing `reviewer_state` helpers.
    - Returns the shortened, Read-based tick prompt naming those absolute paths and telling
      the model to reply `ok` on effective-ACTIVE or `CronDelete` this cron on missing-either.
  - `watchdog_arm_instruction(interval)` at `:247` becomes
    `watchdog_arm_instruction(proj, interval)` — adds `proj` so it can call the new
    `watchdog_tick_prompt(proj)` and embed the per-project tick prompt in the arm
    instruction. This is a signature change; both callers below update.
- `stopcontinue_watchdog_toggle.py:45-47`: pass `proj` to `rl.watchdog_arm_instruction`.
- `session_start_hook.py:63-65`: pass `proj` to `rl.watchdog_arm_instruction`.
- `reviewer_lib.py`: re-export `watchdog_tick_prompt` alongside the existing
  `watchdog_arm_instruction`; regenerate the facade contract.
- `stopcontinue_watchdog_toggle.py`:
  - The `status` subcommand stays for direct inspection (operator-invoked), so no behavior
    loss for the CLI. What changes is only what the *tick* invokes.
- The `stopContinueWatchdog` SKILL (`skills/stopContinueWatchdog/SKILL.md`): unchanged. It
  describes the toggle, not the tick prompt.

### Docs

- `docs/plans/stopcontinue_watchdog.md`: update the tick-prompt description in the "Mechanism"
  section — the exact tick prompt text lives there.
- `README.md`: no user-facing change (the tick is a background artifact).
- `CHANGELOG.md`: "Changed" entry.

### Not touched

- Stop hook (`stop_hook.py`) — unchanged. The Stop hook is the payload; the visibility change
  is upstream of it.
- `stopcontinue_toggle.py` — unchanged. Independent of the watchdog visibility.
- (`session_start_hook.py` is touched — the signature change to `watchdog_arm_instruction`
  requires passing `proj`. See the code section above; contradicts what an earlier draft of
  this plan said.)

## Tests (red before green, per TDD)

- **New tick-prompt shape.** `stopcontinue_watchdog_toggle.py on` prints an arm instruction
  whose tick prompt (a) is shorter than today's, (b) names the two absolute flag-file paths,
  (c) preserves the "if unfinished work then resume; else reply `ok`" branch, (d) does not
  shell out to any Python script.
- **Recovery branch preserved.** The tick prompt contains a substring equivalent to
  "if you have unfinished work from before, resume it" — no substitution that removes it
  can pass this test. This is the load-bearing rescue path for a stranded session.
- **`SessionStart` re-arm uses the same template.** Both entry points emit an identical
  tick prompt.
- **Prompt-template helper is a single source of truth.** A test asserts both entry points
  call the same helper (introspecting the helper name isn't ideal — better: they both produce
  byte-identical prompts for a fixed project and interval).
- **Legacy behavior guardrails.** `status` still prints `ACTIVE` / `INACTIVE` when invoked
  directly (unchanged for operator use).
- **No behavior change to self-cancel semantics.** Existing test
  `test_streaming_audit_persistent_idle_fails_closed_without_a_third_attempt` and the
  watchdog-toggle test suite pass unchanged.

## Non-goals

- **Zero visible tick.** Impossible under `CronCreate` — a tick IS a turn. Getting to zero
  requires the OS-crontab mechanism from earlier discussion, which trades away automated
  recovery of a stranded interactive TUI. Not this plan.
- **Collapsing consecutive quiet ticks.** Claude Code's `defaultView: chat` already offers
  operator-side quieting for all routine turns, and no per-tick collapse feature exists at
  the CronCreate layer for us to hook.
- **Changing `on`'s stopContinue-dependency check** or the `off` behavior — separate concern.
- **Changing the tick interval** — separate concern (the 7 min default is unchanged; operator
  can already tune it via `on <interval>`).

## Open questions

- **Does Claude Code's Read tool actually render a one-line file more compactly than a
  one-word-output bash tool call?** Assumed based on tool card conventions; if it turns out
  the two are equivalent in rendering, the switch is still worth it because Read avoids
  spawning a subprocess (slightly faster tick) but the visibility win is smaller. Worth
  measuring on the first live tick post-ship.
- **Should the tick prompt paths be recomputed at tick time or baked at arm time?** Baked at
  arm time is simpler and matches how the current prompt embeds the toggle script path.
  Recompute-at-tick would need the model to look up config, which reintroduces a tool call.
  Bake at arm time it is; document that a moved `REVIEWER_STATE_DIR` mid-session leaves the
  cron pointing at stale paths — but that's an existing limitation of any session-scoped
  cron, not new.
