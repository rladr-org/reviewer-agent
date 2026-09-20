# Watchdog auto-shutoff / auto-rearm

## Why

`stopContinueWatchdog` fires a cron tick every N minutes (default 7) as long as the
per-project flag is on. On a session that finished its work and went idle, that produces
a visible tick every N minutes indefinitely — ~200 ticks/day of noise for a session with
nothing to do. The operator's only lever today is to run `/stopContinueWatchdog off` and
`/stopContinueWatchdog on` by hand at the boundaries of their work.

This plan makes the watchdog **auto-shutoff** after a session has been idle for a full
interval and **auto-rearm** when the operator's next prompt signals new work. Steady state:
silence while idle, then re-arm on the next prompt.

## The hard constraint that shapes everything

`CronCreate` / `CronList` / `CronDelete` are Claude Code **session-local model tools**. A
Python hook cannot call them. So the *actual* teardown of the cron must always be performed
by a model turn — there is no hook-only path to delete a cron.

The prior draft tried to force the teardown by **hijacking** the end-of-work STOP into a
`KEEP_WORKING` that asked the model to run `CronDelete` right then. That put a *new, unproven*
model-compliance point on the critical path (a "done" reply without the tool call left the
cron alive forever), required a control-turn exemption, and showed the operator a fake "keep
working" at every work-session end.

**This plan splits the decision from the execution, and keeps every time/threshold judgement
in Python — never in the model:**

- **Decision → the Stop hook, in Python.** On a genuine completion it writes a session-scoped
  **disarm** marker (a timestamp). On each later idle tick it checks, *in Python with a
  reliable clock*, whether a full interval has elapsed; once it has, it writes a session-scoped
  **teardown_ready** marker. No time reasoning is ever delegated to the model.
- **Execution → the tick, reduced to a pure presence check.** The tick already self-cancels
  when a flag file is missing (proven, live). We add: *also tear down if `teardown_ready`
  exists.* The tick does no clock arithmetic — it only checks whether files exist, and it
  **fails safe** (if it cannot read a file, it does not delete). A surviving cron keeps
  ticking, so an ignored teardown is retried next tick; a successful teardown removes the cron,
  so ticks stop on their own.
- **Re-arm → the Stop hook's signal, with two reliable fallbacks.** `UserPromptSubmit` re-arms iff
  `teardown_ready` exists, OR (defensively, and still reliably in Python) the disarm marker is
  present and older than one interval, OR the cron looks **dead** — no tick seen for more than two
  intervals. All three are hook-side clock checks; the model never gates re-arm. The third covers
  the case the first two cannot see at all: a cron that vanished without any marker being written,
  which is what a host restart does. See *Tick liveness* below.

The model-compliance surface stays at exactly the existing tick (a presence check), and its
**worst case degrades to today's behavior** (a tick per interval while the model keeps ignoring
`CronDelete`), never to silent failure.

## Design principles

1. **Flag and cron are separate concepts.** The per-project `stopcontinue_watchdog.active`
   flag is *operator intent*, toggled only via `/stopContinueWatchdog on|off`. The cron is the
   *active poke*, torn down and re-armed automatically. Auto-shutoff never touches the flag.
2. **All timing lives in Python hooks; the model only checks file presence.** This is the
   central reliability decision (see the two rejected alternatives below).
3. **Disarm on a genuine STOP_OK — including a recovery tick.** The disarm→teardown machinery
   engages when the supervisor's verdict is STOP_OK (the task is judged complete), whether that
   STOP_OK ends an ordinary turn OR a **tick that resumed and finished a stranded task**. Every
   other allow-stop path — tool-only final turn, nudge-cap-reached, background-wait park,
   codex-failure fail-open — leaves the watchdog armed, because those are exactly the
   incomplete/undetermined strands the watchdog exists to recover. To keep the idle countdown
   honest, a STOP_OK writes the disarm marker only when none already exists (a heartbeat tick
   must never reset an aging marker's `ts`).
4. **Graceful degradation, never silent failure.** An ignored teardown tick is retried next
   interval; an early/erroneous delete is recovered by the re-arm fallback. Do **not** count
   `SessionStart` as that recovery: it emits its arm instruction but the host does not deliver the
   hook's `additionalContext` on a **resumed** session (measured 2026-09-19), so on the restart path
   it recovers nothing. Tick liveness is what actually closes that hole.
5. **The operator never sees a fake "keep working."** Real STOP_OK stays STOP_OK. The only
   visible artifact of a shutoff is the idle tick(s) leading up to it, and only after a genuine
   idle interval.
6. **Session-scoped markers.** Each session owns its own markers; see section 1.

## Two rejected alternatives (each caught in review)

1. **Tear down on marker *presence* (no interval gate).** A `*/N` cron is wall-clock-aligned,
   so a STOP_OK at 14:06:50 would be torn down by the 14:07:00 tick — seconds later, while the
   operator may still be working — churning a teardown/re-arm cycle. **Rejected:** teardown must
   wait until the session has actually been idle for an interval, enforced by the Stop hook's
   in-Python age check before it writes `teardown_ready`.
2. **Let the model compare the current time to a deadline in the tick.** The tick's clock
   acquisition is unspecified and unreliable; the model could delete early (→ silent protection
   loss when a later prompt clears the marker without re-arming) or loop. **Rejected:** the tick
   does no time comparison at all — it checks `teardown_ready` presence. All timing is done in
   Python where the clock is reliable.

## Mechanism

### 1. Three per-session state files

Both keyed by `claude_session_id = claude_session_key(data)` (the same key `reviewer_state.py`
already uses for per-session reviewer isolation):

- `watchdog_disarm_file(proj, sid)` — JSON `{"ts": "<iso stop time>", "session_id": "<sid>"}`.
  Written by the Stop hook on a genuine STOP_OK. `ts` is load-bearing (the in-hook age check).
- `watchdog_teardown_ready_file(proj, sid)` — presence only. Written by the Stop hook once an
  idle tick observes that `now − disarm.ts ≥ interval`. This is the tick's teardown trigger AND
  the primary re-arm signal.
- `watchdog_tick_seen_file(proj, sid)` — JSON `{"ts": "<iso tick time>", "session_id": "<sid>"}`.
  Written by `UserPromptSubmit` when a tick actually arrives, and again when an arm is requested.
  `watchdog_ticks_presumed_dead()` reads it: absent, unreadable, or older than **two** intervals
  means the cron is gone.

  **Its fail direction is the opposite of the other two, deliberately.** An absent or corrupt
  `disarm` must never read as "old enough to tear down", because tearing down wrongly loses the
  watchdog. An absent or corrupt `tick_seen` must read as **dead**, because the only cost of being
  wrong is one redundant arm instruction, while being wrong the other way leaves the watchdog
  silently absent — which is the failure this file exists to prevent.

  **Two intervals, not one:** a tick only fires while the REPL is idle, so a genuinely busy session
  can miss one without anything being wrong. Writing the marker on *arm* as well as on *tick* bounds
  re-asking to once per window rather than once per prompt during a long busy stretch.

  It is never written on an ordinary prompt. Doing so would keep it permanently fresh and mask a
  dead scheduler forever — the one implementation mistake that would pass every other test here.

**Why session-scoped.** `CronCreate` is session-local: each session registers its own cron, and
a session's `CronList`/`CronDelete` only see that session's crons. A project-scoped marker would
let session A's stop drive session B's tick to tear down session B's *own* live cron. All three
markers share one session-scoped stem — `wd_<sha1(canonical project + session)[:20]>`, plus
`.disarm` / `.teardown_ready` / `.tick_seen` (`_watchdog_marker_stem`) — so each session owns only
its own; the arm-instruction bakes the session-specific `teardown_ready` path into the tick prompt
(section 3). The stem is a fixed-length digest rather than a readable path because the readable
form grows with the project path and, with the atomic writer's temp sibling on top, could exceed
the filesystem's name limit — at which point the Stop hook's fail-open would swallow ENAMETOOLONG
and auto-shutoff would silently stop. Orphan markers from a killed session are harmless — their
session id never appears in a live session's hook, tick, or prompt.

### 2. `stop_hook.py`: the decision (pure Python, reliable clock)

Existing stopContinue supervision is unchanged. The decision is added with this precedence.
**Both marker writes are gated on a genuine STOP_OK verdict (step 4), never on marker age
pre-supervision (see 2c) — arming teardown on age alone would delete the watchdog on a tick that
resumed incomplete work or took an early return.**

1. Compute flag state and `sid = claude_session_key(data)`, and whether this is a tick turn via
   `_last_turn_prompt_text(events)` (2a): the main-session turn-start begins with
   `stopContinueWatchdog tick`.
2. **Marker-clear-first invariant.** If the watchdog flag is **off**, clear **both** markers for
   `sid`, then pass through. Their meaning is invalid under flag-off; any present marker is
   stale. Idempotent, and it runs before the early returns so a control turn that turns the
   watchdog off still clears its markers. (A stale marker under a fully-inactive stopContinue is
   also benign, so this need not be forced ahead of the stopContinue-inactive early return.)
3. **Supervise as today** — no watchdog marker write happens on any of the allow-without-STOP_OK
   early returns (tool-only, background-wait park, nudge-cap): those leave the watchdog armed.
4. **On a genuine STOP_OK verdict (after supervision):**
   - if this is a **tick** turn AND the disarm marker has aged past one interval
     (`now − disarm.ts ≥ watchdog_interval(proj)`, reliable Python clock) → write
     `teardown_ready`; the next tick tears the cron down. A tick only reaches here when it
     genuinely completed (a healthy `ok`, or a recovery that finished its work), so this never
     arms teardown while work may remain.
   - set the disarm marker **if none exists** (`ts = now`) — a heartbeat tick never resets an
     aging marker, while a completion (an ordinary turn, a **recovery tick that resumed a stranded
     task to completion**, or the first idle tick of an armed-but-never-worked session) starts the
     countdown.
   - a **non-tick** STOP_OK also clears any pending `teardown_ready` (a fresh completion restarts
     the idle cycle).
   **Every other real-turn outcome — KEEP_WORKING, tool-only allow, nudge-cap allow,
   background-wait park, fail-open — writes nothing** (principle 3).

Gone versus the prior draft: the hijacked `KEEP_WORKING`, its cleanup prose, the "second stop"
branch, and the control-turn exemption (2b).

### 2a. `_last_turn_prompt_text`: main-session turn-start detection

Tick detection must NOT reuse `_is_human_prompt`: verification (see "Verification findings")
shows a fired tick is a `type: user` event with `isMeta: True`, which `_is_human_prompt` filters
out — so keying on "last human prompt" finds the *previous real* prompt and misclassifies every
tick as a real turn. The new helper in `reviewer_transcript.py` returns the text of the most
recent **main-session turn-start** user event: scan backward, skip assistant events, skip
`tool_result` carriers (a `toolUseResult` event or content holding a `tool_result` block), **skip
`isSidechain` user events** (a subagent's prompt must never be mistaken for the current main
turn), and return the first remaining user event's text — `isMeta` turn-starts (the tick,
injected feedback) INCLUDED. Because root hooks reach transcript helpers through the facade, it is
re-exported via `reviewer_lib.py` and added to the generated contract, like
`resolve_transcript_path`; otherwise `rl._last_turn_prompt_text(...)` from `stop_hook.py` would
not resolve.

### 2b. Why control-turn detection is no longer needed

A `/stopContinueWatchdog on` turn's STOP_OK writes a disarm marker; the cron is torn down only if
the operator then stays idle a full interval, at which point the flag stays on and the next
prompt re-arms. That is the intended cron-follows-activity / flag-follows-intent semantics, and
it is self-healing (contrast the hijack design, which deleted the just-armed cron the same turn).
A control `off` turn takes the flag-off branch (step 2), which clears both markers.

### 2c. Only a STOP_OK verdict arms teardown (the incomplete-stop exclusions)

The Stop hook has paths that allow a stop WITHOUT a STOP_OK verdict: background-wait park (harness
auto-re-invokes), tool-only final turn, nudge-cap-reached — plus KEEP_WORKING, where the agent
keeps working. In every one the work may be incomplete, so the watchdog must stay armed to recover
the strand. Therefore **both** the disarm write and the `teardown_ready` arm live only in the
STOP_OK branch (step 4), never pre-supervision on marker age.

An earlier draft armed `teardown_ready` pre-supervision "so a tool-only tick still advances it."
That was a bug: an aged tick that resumes work and gets KEEP_WORKING, or that takes a
tool-only / bg-wait / nudge-cap early return, would arm teardown, and the next tick would delete
the watchdog **despite incomplete work**. Gating on the STOP_OK verdict is the fix. The only cost
is that an idle tick whose final message happens to be tool-only does not advance `teardown_ready`
that tick; the next healthy `ok` tick does — bounded, and never a spurious teardown.

### 3. `watchdog_tick_prompt` / the tick: the execution (pure presence check, fail-safe)

The tick reads only file *presence* — no clock. Because the marker is session-scoped,
`watchdog_tick_prompt` needs the session id to bake the path; its signature becomes
`watchdog_tick_prompt(proj, sid)`, and `watchdog_arm_instruction(proj, interval, sid)` threads it
through (its three callers — SessionStart, `UserPromptSubmit`, and the toggle — all resolve a
session id; see section 5 for the toggle).

Updated tick prompt shape (`<ready>` is the session-scoped `teardown_ready` path baked at arm
time; `<wd>`/`<sc>` are the existing project-scoped flag paths):

> stopContinueWatchdog tick -- Read `<ready>`, `<wd>` and `<sc>`; if `<ready>` exists OR either
> flag file is missing, CronList and CronDelete every task whose prompt contains
> "stopContinueWatchdog" and stop. If you cannot read those files, do NOT delete anything —
> reply `ok` and stop. Otherwise: if you have unfinished work from before, resume it; else reply
> `ok` and stop.

The tick never deletes `teardown_ready` (a surviving cron re-tears-down next tick — the retry),
and the explicit "if you cannot read those files, do NOT delete" clause is the fail-safe: on any
uncertainty the tick keeps the watchdog armed rather than removing it.

### 3a. Upgrade path: the reconcile must be prompt-aware

This plan CHANGES the embedded tick prompt (it now reads `teardown_ready` instead of only the
flag files). Today's arm/reconcile contract keeps any marker-tagged task already at schedule
`*/N` **without inspecting its prompt** (`watchdog_arm_instruction` in `reviewer_prompts.py`;
`skills/stopContinueWatchdog/SKILL.md`), so a cron still carrying the old flag-only tick would be
kept and would never consult `teardown_ready` — the new teardown would silently never fire, and
even a later re-arm would keep the stale cron. (Crons are session-local and in-memory, so in
practice the stale case is a session that armed under old code; but the reconcile contract must
not rely on that.)

Fix: make the reconcile **prompt-aware**. The arm-instruction already embeds the exact current
tick prompt verbatim, so the reconcile keeps a marker-tagged task ONLY when its schedule is `*/N`
**and its prompt matches the current tick prompt**; every other marker-tagged task — wrong
schedule OR an outdated/different prompt — is `CronDelete`d, and if none match, the current tick
prompt is `CronCreate`d. This forces any outdated-prompt cron to be deleted and recreated on the
next arm (SessionStart, `UserPromptSubmit` re-arm, or the toggle), so the new `teardown_ready`
tick actually takes effect. Tick DETECTION continues to key on the version-stable prefix
`stopContinueWatchdog tick` so a prompt-body change never breaks tick-vs-real classification.

### 4. `UserPromptSubmit` hook (new): auto-rearm + marker clear

Register a `UserPromptSubmit` hook wired to a new `userpromptsubmit_hook.py`. On each operator
prompt:

- Read stdin (`cwd`, `session_id`, prompt text).
- With `sid = claude_session_key(data)`: if `is_stopcontinue_watchdog_effective(cwd)` is false →
  `clear_watchdog_tick_seen(proj, sid)` and emit nothing. The clear matters as much as the silence:
  with `stopContinue` off the tick tears its own cron down, so a stamp left behind would make a dead
  cron look alive and suppress the re-arm after re-enabling. This gate is deliberately **before**
  the tick check below, so operator intent outranks liveness.
- If the prompt looks like a watchdog tick (marker substring) → `set_watchdog_tick_seen(proj, sid)`
  and emit nothing. The tick reaches the agent through this hook, so liveness is observed for free;
  arming in response to a tick is still forbidden (defense in depth).
- Otherwise compute (all in Python):
  `rearm = is_watchdog_teardown_ready(proj, sid) OR (is_watchdog_disarm_set(proj, sid) AND
  now − disarm.ts ≥ watchdog_interval(proj)) OR watchdog_ticks_presumed_dead(proj, sid)`.
  Clear the disarm and teardown_ready markers for `sid`. **Iff** `rearm`: also
  `set_watchdog_tick_seen(proj, sid)` — asking counts as liveness for one window, so a long busy
  stretch that legitimately produces no ticks does not append an arm block to every prompt — then
  emit `additionalContext` carrying
  `watchdog_arm_instruction(proj, watchdog_interval(proj), sid)`.

**Why the three-term gate.** `teardown_ready` is the normal signal (a tick reached the interval and
tore down). The age fallback closes the residual hole where the model deleted the cron *before*
`teardown_ready` was written: on the next prompt after a full idle interval the fallback re-arms
anyway. The liveness term closes a hole neither of the first two can see, because it leaves no
marker at all — a cron that simply **ceased to exist**, which is what a host restart does, and also
what a hand-deleted cron, the seven-day auto-expiry and a crashed scheduler do.

All three are false during active work with a live cron (no idle interval elapsed, no
`teardown_ready`, ticks arriving), so there is no per-prompt reconcile noise; the reconcile runs
after genuine idle or genuine death, where it is appropriate (and idempotent if the cron happens to
still be live). This is why the toggle need not clear markers (section 5).

**The liveness term is bounded, not instant.** `tick_seen` is on disk and survives the restart that
killed the cron, so a restart that lands within two intervals of the last tick leaves the marker
looking fresh: the cron is gone but no prompt re-arms until the marker ages out. Worst case is
therefore roughly two intervals of no watchdog after a restart, and the operator sees `CronList`
empty during that window. That is the deliberate trade for not re-arming on every prompt of a busy
session; shortening it means accepting that noise instead.

### 5. `stopcontinue_watchdog_toggle.py`: resolve the session id from the environment

`/stopContinueWatchdog on` emits the arm-instruction the model executes to `CronCreate` the tick
this session, so it must bake the session-scoped `teardown_ready` path — which needs the session
id. The toggle receives only argv, but Claude Code exports the session id in the environment:
`CLAUDE_CODE_SESSION_ID` (legacy `CLAUDE_SESSION_ID`), whose value equals the transcript stem —
the SAME key the hooks derive via `claude_session_key`, so the toggle-baked path and the
hook-written path match exactly. Reuse the existing resolver convention from
`reviewer_session_toggle.py` (factored into a shared `reviewer_state` helper re-exported via
`reviewer_lib`, used by both toggles). `on` resolves the session id and passes it to
`watchdog_arm_instruction(root, interval, sid)`; if neither variable is set it **fails closed**
with an error rather than arming an unscoped or wrong-session cron (guessing would risk tearing
down another session's cron — the same fail-closed stance `reviewer_session_toggle.py` takes).

The toggle does NOT clear markers (a stale marker is benign — at most one idempotent reconcile —
and clearing is handled where the session id is reliably present: the Stop hook flag-off invariant
and `UserPromptSubmit`).

`tests/test_stopcontinue_watchdog_toggle.py` must be updated: the `on` tests set
`CLAUDE_CODE_SESSION_ID` and assert the emitted arm-instruction embeds the session-scoped
`teardown_ready` path; add a case asserting `on` fails closed when the variable is unset.

### 6. `session_start_hook.py`: emits an arm the host may not deliver

SessionStart still emits the arm instruction and threads its session id into
`watchdog_arm_instruction`. Treat it as **best-effort only, not the backstop.** On a *resumed*
session the host runs the hook but does not deliver its `additionalContext`: measured 2026-09-19,
where the hook logged `watchdog re-arm context injected` while the session transcript recorded no
`hook_additional_context` entry at all, and the same hook at a non-resume start did record one.
The log line reports *emission*, not delivery, so the failure is silent.

The real backstop is the liveness term in section 4. On the first cold restart after a fresh
install this was observed working end to end: `[session_start] … injected` at 14:10:31 reaching
nothing, then `[userpromptsubmit] … injected` at 14:10:35 reaching the agent and re-arming.

## Scope: Claude Code host only

`stopContinueWatchdog` is Claude Code-only — it relies on Claude Code's `CronCreate`/`CronList`/
`CronDelete` session tools and the SessionStart hook + skill injection to arm. The Codex-native
host under `plugins/reviewer-agent-codex/` has no watchdog concept
(`grep -r "watchdog\|Watchdog\|Cron" plugins/reviewer-agent-codex/` returns nothing). This plan
modifies **only** the Claude Code host at the repository root; the Codex plugin is untouched. The
pi/claude "backends" referred to elsewhere are reviewer *backends* inside the Claude Code host,
not the two plugin hosts; all three share this flow.

## Files that must change

### Code

- `reviewer_state.py`: add session-scoped `watchdog_disarm_file` / `is_watchdog_disarm_set` /
  `set_watchdog_disarm` (writes `ts`) / `read_watchdog_disarm_age` (or a `disarm_deadline_passed`
  helper) / `clear_watchdog_disarm`, and `watchdog_teardown_ready_file` /
  `is_watchdog_teardown_ready` / `set_watchdog_teardown_ready` / `clear_watchdog_teardown_ready`,
  all taking `(proj, claude_session_id)`. Add a shared `claude_session_id_from_env()`
  (`CLAUDE_CODE_SESSION_ID` / `CLAUDE_SESSION_ID` resolver factored out of
  `reviewer_session_toggle.py`).
- `reviewer_transcript.py`: add `_last_turn_prompt_text(events)` — most recent main-session
  turn-start text (includes `isMeta`; excludes `tool_result` carriers AND `isSidechain`). See 2a.
- `reviewer_prompts.py`: `watchdog_tick_prompt(proj)` → `watchdog_tick_prompt(proj, sid)` (read
  `teardown_ready` + fail-safe clause); `watchdog_arm_instruction(proj, interval)` →
  `watchdog_arm_instruction(proj, interval, sid)`; and make its reconcile prose **prompt-aware**
  (keep a marker cron only if schedule AND prompt match the current tick prompt; delete + recreate
  any outdated-prompt cron — section 3a). Keep the tick-detection prefix version-stable
  (`stopContinueWatchdog tick`).
- `skills/stopContinueWatchdog/SKILL.md`: update the reconcile description to the prompt-aware
  contract (section 3a) so the operator-facing skill matches the arm-instruction.
- `stop_hook.py`: add the decision — flag-off clears both markers (before the early returns);
  compute `is_tick` via `_last_turn_prompt_text`; and, only on a genuine STOP_OK verdict, arm
  `teardown_ready` when `is_tick` and `now − disarm.ts ≥ interval`, then set disarm if absent (a
  non-tick STOP_OK also clears `teardown_ready`). KEEP_WORKING and the tool-only/background-wait/
  nudge-cap early returns write nothing — so an aged tick that resumes work or takes an early
  return never arms teardown while work may remain (2c).
- `userpromptsubmit_hook.py`: new file. Clears both markers; emits the arm-instruction iff
  `teardown_ready` present or the disarm age fallback holds.
- `hooks/hooks.json`: add the `UserPromptSubmit` entry → `userpromptsubmit_hook.py`.
- `stopcontinue_watchdog_toggle.py`: resolve session id from env, pass to
  `watchdog_arm_instruction`, fail closed if absent (section 5).
- `session_start_hook.py`: thread the session id into `watchdog_arm_instruction`.
- `reviewer_lib.py`: re-export the new state helpers, `claude_session_id_from_env`,
  `_last_turn_prompt_text`, and the changed prompt signatures.
- `reviewer_session_toggle.py`: use the shared env resolver (de-duplicate its local `_session_id`).
- `tests/test_facade_contract.py`: regenerate via `tools/gen_facade_contract.py`.
- `tests/test_stopcontinue_watchdog_toggle.py`: update `on` tests for the session-id source.

### Docs

- `docs/plans/stopcontinue_watchdog.md`, `docs/plans/watchdog_tick_visibility.md` (cross-ref),
  `README.md`, `CHANGELOG.md`: describe auto-shutoff on idle + auto-rearm on next prompt.

## Verification findings (done — 2026-08-29)

Ran the real `reviewer_transcript` helpers against a live transcript containing a fired tick:

- **A fired tick is a `type: user` event with `isMeta: True`, plain-`str` content beginning
  exactly with `stopContinueWatchdog tick --`.** It is the turn-start; its `CronList`/`CronDelete`
  tool-results are separate `toolUseResult` carriers.
- **`_is_human_prompt` returns `False` for it** (it filters `isMeta`), so the original "last human
  prompt" detection was WRONG. Corrected in 2a with `_last_turn_prompt_text`.
- `"stop hook feedback:"` is already in `_NON_HUMAN_USER_PREFIXES`; a KEEP_WORKING continuation's
  injected feedback is returned by `_last_turn_prompt_text` (a main-session turn-start) but does
  not start with the tick marker, so it is correctly a non-tick turn.

## Tests (red before green, per TDD)

**State helpers:** disarm roundtrip (set / is-set / age-or-deadline / clear) and teardown_ready
roundtrip, session-scoped (session B never sees A's markers; distinct paths).
`claude_session_id_from_env` reads `CLAUDE_CODE_SESSION_ID` (and the legacy name), None when unset.

**Decision (stop_hook):**
- Real STOP_OK, flag on, non-tick → verdict unchanged as **STOP_OK** (NOT hijacked); disarm set
  (fresh `ts`), teardown_ready cleared.
- Real tool-only allow / nudge-cap allow / **background-wait park**, flag on → disarm NOT written
  (watchdog stays armed to recover the strand).
- Real KEEP_WORKING, flag on → nothing written.
- **STOP_OK tick with a stale disarm (age ≥ interval)** → teardown_ready now set; disarm NOT
  refreshed (`ts` unchanged). *(Simulate by writing disarm with an old `ts`.)*
- **STOP_OK tick with a fresh disarm (age < interval)** → teardown_ready NOT written (no premature).
- **Aged tick that gets KEEP_WORKING** (resumed a stranded task, not done) → teardown_ready NOT
  armed (only a STOP_OK verdict arms it; the watchdog stays up while work remains).
- **Aged tick whose final turn is tool-only** (takes the tool-only early return before
  supervision) → teardown_ready NOT armed.
- **Recovery tick: tick turn, STOP_OK verdict, NO disarm marker present** → disarm now set
  (`ts ≈ now`), so the recovered-then-idle session goes on to produce teardown_ready and shut
  off (covers the "tick resumes all unfinished work then STOP_OK" path).
- **Idle heartbeat tick: tick turn, STOP_OK verdict, disarm ALREADY present** → disarm `ts`
  unchanged (a STOP_OK writes disarm only if absent).
- Flag off (stale markers present) → both cleared.

**Execution (tick prompt):**
- `watchdog_tick_prompt(proj, sid)` embeds the session-scoped teardown_ready path and both flag
  paths; exactly one CronDelete; teardown_ready + both flag paths precede it; teardown is a real
  conditional ("teardown_ready exists OR either flag missing"); a fail-safe "cannot read → do NOT
  delete" clause is present; the teardown_ready path never appears at/after the CronDelete.

**Detection helper (`_last_turn_prompt_text`):**
- Returns the `isMeta` tick turn-start (and `_is_human_prompt` does NOT — encodes the finding).
- Returns a plain human turn-start for a real turn.
- **Skips `isSidechain`:** a transcript whose most recent user event is a subagent prompt returns
  the earlier MAIN turn-start.
- Skips `tool_result` carriers.

**Auto-rearm (UserPromptSubmit):**
- Effective + teardown_ready present → emits the arm-instruction AND clears both markers.
- Effective + no teardown_ready but disarm age ≥ interval → emits the arm-instruction (the
  fallback that closes the early-delete hole).
- Effective + fresh `tick_seen` + disarm present but age < interval, no teardown_ready → emits
  **nothing**, clears both (the active-work case).
- Effective + **no `tick_seen`** → emits the arm-instruction (the cron is presumed dead; this is
  the restart path, and the redundant-arm case described under *End-to-end* below).
- Effective + `tick_seen` older than two intervals → emits the arm-instruction.
- Effective + `tick_seen` unreadable/corrupt → emits the arm-instruction (fails toward arming).
- Effective + fresh `tick_seen`, no other marker → emits **nothing**, and must **not** refresh
  `tick_seen` (an ordinary prompt never stamps; stamping here would mask a dead scheduler forever).
- Arming **from this hook**, for any of the reasons above → also stamps `tick_seen`, so the next
  prompt inside the window is silent. (Arms emitted by `SessionStart` or the toggle stamp nothing;
  that asymmetry is deliberate — see *One redundant arm per arm cycle* below.)
- Session A's fresh `tick_seen` must not make session B look alive.
- Not effective (watchdog flag off, **or** stopContinue off) → nothing, **and clears `tick_seen`**
  so a stale stamp cannot survive off→on and suppress the re-arm re-enabling needs.
- Prompt body contains the tick marker → nothing, but **stamps `tick_seen`** (liveness observed for
  free); when not effective, stamps nothing.

**Toggle:** `on` with `CLAUDE_CODE_SESSION_ID` set → arm-instruction embeds the session-scoped
teardown_ready path. `on` with neither env var set → fails closed (nonzero, no arm).

**Manifest:** `hooks/hooks.json` registers `UserPromptSubmit` → `userpromptsubmit_hook.py`.

**Upgrade path (prompt-aware reconcile):** `watchdog_arm_instruction(proj, interval, sid)` embeds
the current tick prompt AND directs keeping a marker cron only when its prompt MATCHES it —
deleting and recreating a same-schedule marker cron whose prompt differs (e.g. an older flag-only
tick). Assert the instruction requires matching/replacing the tick prompt (not schedule-only
keeping), so an outdated-prompt cron is upgraded to the `teardown_ready` tick.

**Session-scoping:** sessions A and B; A's stop sets `disarm(A)`; B's tick (baked with
session_id(B)) reads a different teardown_ready path; B's stop writes `disarm(B)` and leaves A's
markers untouched.

**End-to-end:** flag on → first prompt (no `tick_seen` yet → treated as dead → **re-arms**, and
stamps `tick_seen`; see *One redundant arm per arm cycle* below) → second prompt (`tick_seen` fresh →
silent) → work STOP_OK (disarm set, verdict STILL STOP_OK) → idle: two ticks, each stamping
`tick_seen`, with the age check turning teardown_ready on → teardown tick (teardown_ready present →
CronDelete shape) → prompt (teardown_ready present → re-arm, markers cleared). Assert no
KEEP_WORKING ever emitted.

**Tick liveness:** absent marker → dead; corrupt marker → dead (fail toward arming); fresh →
alive; older than two intervals → dead; an ordinary prompt must **not** refresh the marker (an
implementation that stamped on every prompt would mask a dead scheduler forever while passing every
other case here); session A's fresh marker must not make session B look alive; disabling either
half of `stopContinue`+watchdog clears the marker so a stale stamp cannot survive off→on and
suppress the re-arm that re-enabling needs.

**One redundant arm per arm cycle, accepted deliberately.** Neither `SessionStart` (section 6) nor
the toggle (section 5) records `tick_seen` — they emit the arm instruction and nothing else. So after
a successful arm from either, the first ordinary prompt still sees no marker, classifies the cron as
dead, and asks for a reconcile it does not strictly need. This is bounded to **one** prompt per arm
cycle — not per session, since disabling clears `tick_seen`, so an off→on toggle starts a fresh
cycle and earns one more — and is harmless, because the arm instruction reconciles
to *exactly one* cron rather than creating a second. The alternative — having every arm source stamp
`tick_seen` — would suppress the liveness check precisely when an emitted arm was never delivered,
which is the failure this whole mechanism exists to catch. Prefer one redundant reconcile over a
silent hole.

## Non-goals

- **Zero teardown latency.** Teardown lands within ~2–3 cron intervals of going idle (the Stop
  hook must observe an idle tick past the interval, then a further tick tears down); invisible
  (the operator is idle during it).
- **Auto-turning-off the flag.** Auto-shutoff cancels the cron, not the flag.
- **Distinguishing "done for the session" from a brief pause.** The interval is the threshold,
  checked in Python.
- **Guaranteeing the arm itself against model non-compliance.** Arming still relies on the model
  executing the injected arm-instruction (SessionStart on a **cold** start only — it is not the
  backstop on a resumed one, section 6; UserPromptSubmit after idle or on a dead cron; the toggle on
  `on`), and teardown relies on the tick honoring a presence check. A model that
  CronDeletes against an explicit "reply ok" instruction (not a misjudgement — the tick has no
  clock to misjudge) could drop protection until a later prompt's liveness check; this is a
  pre-existing,
  low-stakes property (the watchdog is a recovery convenience, not a safety-critical gate), and
  the re-arm age fallback recovers it on the next post-idle prompt.
- **Detecting activity via anything other than `UserPromptSubmit`.**
  (The armed-but-never-worked idle case — a session that arms and then sits idle without ever
  doing work — is now handled rather than punted: its first idle tick replies `ok`, the
  supervisor returns STOP_OK, and step 4 writes disarm because none exists, so the session then
  counts down and shuts off like any other.)

## Open questions

- **Teardown latency vs idle-tick count.** The age check writes `teardown_ready` at the first
  idle tick past the interval, and the *next* tick tears down — ~2–3 ticks total. If fewer idle
  ticks matter more than the extra safety margin, `teardown_ready` could be written at the first
  idle tick unconditionally; not worth the churn risk today.
- **Orphan-marker GC.** Killed sessions leave harmless orphan markers; fold a sweep into the
  session-start orphan sweep only if the file count grows.

## Note on the retry / "how many ticks" claim

Auto-shutoff costs a small, bounded number of idle ticks (the age-check tick(s) plus the teardown
tick) *when the model honors the CronDelete* — the common case. If it ignores the CronDelete, the
surviving cron ticks again next interval and retries, so the worst case is the same
one-tick-per-interval as today until the model complies or the operator prompts (which re-arms and
reconciles to one). The design guarantees graceful degradation to today's behavior, never silent
failure.
