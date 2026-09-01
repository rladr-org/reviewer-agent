# `/stopContinueWatchdog` — recover an idle-stranded agent

> **Update:** the cron no longer ticks forever while a session is idle. It **auto-shuts-off**
> within a couple of idle intervals (the first idle tick past the interval arms a `teardown_ready`
> marker; the next tick deletes the cron) and **auto-rearms** on the operator's next prompt (the flag —
> operator intent — is untouched). The decision is made deterministically in the Stop hook
> (Python), the tick is reduced to a fail-safe presence check, and a new `UserPromptSubmit` hook
> re-arms. See `docs/plans/watchdog_auto_shutoff.md` for the mechanism; the "flag on → ticks run
> forever" behavior described below is the pre-auto-shutoff baseline.

## Why

stopContinue supervises **only at a turn boundary**: `stop_hook.py` runs when the agent ends its
turn. It is **fail-open** — `stop_hook.py` computes
`keep_going = code == 0 and bool(out.strip()) and not rl.is_stop_ok(out)`, so any supervisor
timeout, non-zero exit, or empty verdict lets the stop proceed. `STOP_TIMEOUT = 300`
(`stop_hook.py:35`) and the hook's own `hooks.json` timeout is 330s.

That leaves an unrecovered failure mode: **the supervisor API times out (or the main model's
connection drops mid-response) → the hook fails open → the agent goes idle → nothing ever re-fires
the Stop hook.** The agent is stranded with no retry. Observed live 2026-08-25 (`Connection lost
mid-response`), and the exact shape the operator asked to close.

## The platform constraint (verified, not assumed)

There is **no supported way to wake an idle interactive TUI session reactively from outside.**
Confirmed three ways (Claude Code docs, the filesystem, and the daemon architecture):

| claim | status | source |
|---|---|---|
| No IPC / socket / signal / file-watch injects input into a running interactive session | DOCUMENTED | CLI + hooks + sessions docs |
| A hook cannot force a tool call, enqueue a turn, or inject a prompt | DOCUMENTED | hooks reference |
| A bare OS process a hook spawns does NOT trigger re-invocation (only harness-tracked tasks do) | DOCUMENTED | tools reference |
| `Notification`/`idle_prompt` hook fires when the session goes idle but is **output-only** | DOCUMENTED | hooks reference |

The only **supported** re-invocation of an idle session is a **timer the session itself armed**:

| primitive | who starts it | local? | note |
|---|---|---|---|
| `/loop <interval> <prompt>` | **human only** — model cannot emit slash commands | local | fires during idle |
| `CronCreate(cron, prompt)` | **model** (tool call) | **local session task** | the arming primitive |
| `ScheduleWakeup(ms)` | model | — | only RE-paces an already-running dynamic `/loop`; cannot start one |
| `RemoteTrigger` | model | **cloud** | wrong — would not wake the local TUI |

**Conclusion:** recovering an idle session *requires* a timer armed while the agent was still active,
and `CronCreate` is the only model-callable primitive that provides it. This is a platform ceiling.

> Open validation: the "CronCreate = local, idle-firing" claim is from a doc-based agent read. A
> ~2-minute spike in an **isolated** session (`CronCreate` a `*/1 * * * *` task; confirm it fires a
> turn while the session sits idle) would de-risk it. The operator has chosen to implement without
> blocking on the spike.

## Mechanism (minimal)

The key realization: the **existing** `stop_hook.py` already re-runs supervision and forces
continuation at **every** turn end. So the watchdog needs **no** supervision code of its own — its
only job is to make an idle agent **take a periodic turn**, after which the existing hook does
exactly what it always does.

1. When the per-project flag is on, the session arms `CronCreate('*/N * * * *', <tick prompt>)`.
2. Each fire is a **small model turn**. At its end the existing `stop_hook.py` runs normally:
   pending work ⇒ it blocks the stop and the agent continues; genuinely done ⇒ STOP_OK and it stops.
   **No new supervision, markers, or check script — the Stop hook is reused verbatim.**
3. **Arming happens in the `/stopContinueWatchdog on` turn itself.** That command runs while the
   model is active, so its body instructs the model to call `CronCreate(...)` **then and there** —
   the highest-compliance moment, not a passive hope. `SessionStart` re-injects the arm instruction
   only as a **backstop**, for a new/resumed session where the flag is on but no cron is armed.

The tick prompt and cron identity/idempotency/cancellation are specified under **Scope and cron
lifecycle** below.

## Command

`/stopContinueWatchdog on|off [interval]` — mirrors the existing toggle family
(`stopcontinue_toggle.py`; `/reviewerModel … timeout`).

- **Interval** is in **minutes, 1–59** (default 7), encoded as `*/N` in the cron minute field.
  Exact for divisors of 60 (2,3,5,6,10,12,15,20,30); a small jitter for other N is acceptable for a
  watchdog. `> 59` is rejected — `*/N` in the minute field is not a general arbitrary-minute
  interval, and a multi-hour watchdog defeats the purpose.
- **`on` REQUIRES `stopContinue` to already be active for the project** and errors otherwise
  (`stopContinue: enable it first with /stopContinue on acceptRisk`). This is not optional: the
  whole mechanism leans on the existing Stop hook, and `stop_hook.py:69` returns immediately when
  the `stopContinue` flag is off — so a watchdog without `stopContinue` would fire tick turns that
  are never supervised. Requiring the existing flag also keeps its risk-acceptance gate
  (`DISCLAIMER.md`) intact rather than re-minting autonomy behind a second, un-gated switch.
- **`on`** sets the per-project flag + interval **and, in the same active turn, has the model arm
  the cron** (see Scope and cron lifecycle). **`off`** clears the flag and has the model cancel this
  session's cron via `CronList` → `CronDelete`-by-marker; other sessions' crons self-cancel on their
  next tick.

## Scope and cron lifecycle

**One scope rule, stated once:**

- **The flag and interval are per-PROJECT** (mirroring `stopcontinue_flag_file`) — "watchdog on for
  this repo".
- **A cron is per-SESSION** — `CronCreate` tasks are session-scoped, so each Claude session working
  the project arms its *own* cron.

There is **no persisted task id and no armed-marker file** — both were the source of the scope
muddle and neither can prove the cron is still alive. Instead **`CronList` is the single source of
truth**, and watchdog crons are identified by a fixed marker string in their tick prompt
(`stopContinueWatchdog`):

- **Arm** (the `on` turn, and the SessionStart backstop): the instruction has the model `CronList`
  and **reconcile the complete set of marker-tagged tasks to exactly one** at the target schedule
  `*/N` (N = the stored per-project interval):
  - if one or more marker-tagged tasks are already at `*/N` → keep a single one and `CronDelete`
    **every other** marker-tagged task (duplicates from a prior race, and any left at a stale
    schedule);
  - if none is at `*/N` → `CronDelete` **all** marker-tagged tasks and
    `CronCreate('*/N * * * *', <tick prompt>)`.

  So the post-condition is invariant: **exactly one marker-tagged cron, at `*/N`.** This is
  idempotent (a lone correct cron ⇒ create nothing, delete nothing), self-healing (extras are
  removed), liveness-correct (a dead cron is absent from `CronList` and gets re-created),
  interval-correct (`on 5` after `on 3` reconciles `*/3`→`*/5`) — all from `CronList` as the source
  of truth, with no marker file to go stale.
- **Cancel `off`**: clears the per-project flag, then has the *calling* session `CronList` →
  `CronDelete` every marker-tagged task it owns. Other sessions' crons are **self-cancelling**: the
  tick checks the project flag (below) and deletes itself when the flag is off. So `off` in one
  session flips the flag for all, and every other session's cron removes itself on its next fire —
  eventually-consistent project-wide cancellation without cross-session coordination.

### The tick prompt

Per-project text carrying the `stopContinueWatchdog` marker, built by
`watchdog_tick_prompt(proj, claude_session_id)` in `reviewer_prompts.py`. It is a fail-safe
PRESENCE check that names the session-scoped `teardown_ready` marker and the two flag files and
directs the model to Read each path INDIVIDUALLY -- never one batched `ls`, because `teardown_ready`
is normally ABSENT, so a batched check would exit non-zero on a healthy session (a spurious error,
and a legitimately-missing marker must not be misread as an unreadable filesystem). It tears the
cron down when `teardown_ready` exists OR either flag file is missing, deletes NOTHING when a path
is genuinely unreadable (a merely-missing path is not a read failure), and otherwise resumes any
unfinished work or replies `ok`. All timing is decided in the Stop hook (Python), so the tick reads
short flag files with the Read tool rather than shelling out. See
`docs/plans/watchdog_auto_shutoff.md` and `docs/plans/watchdog_tick_visibility.md`.

## Files (minimal)

- **new** `commands/stopContinueWatchdog.md` + `stopcontinue_watchdog_toggle.py`
  (`on|off|status` subcommands — `status` prints `ACTIVE`/`INACTIVE` for the tick's self-cancel check)
- `reviewer_state.py` — **per-project** flag + interval accessors (mirror `is_stopcontinue_active`);
  no task-id/marker state (CronList is authoritative)
- `session_start_hook.py` — inject `additionalContext` to re-arm **only when the flag is on**; the
  instruction itself checks `CronList` for idempotency/liveness
- `reviewer_prompts.py` (or inline) — the tick prompt (carrying the `stopContinueWatchdog` marker) +
  the CronList-guarded arm / cancel instruction text
- `README.md` / `CHANGELOG.md`
- **tests**:
  - toggle on/off state; interval validation (default 7, floor 1, **reject > 59**)
  - `status` prints `ACTIVE` when the flag is on, `INACTIVE` when off
  - **`on` errors when `stopContinue` is inactive**; succeeds when it is active
  - the `on` command emits the CronList-guarded `CronCreate` arm instruction (mid-session,
    active-model path — NOT SessionStart-only) carrying the marker
  - **`off`** clears the flag and emits the `CronList` → `CronDelete`-by-marker cancel instruction
  - **interval change** (`on 5` after `on 3`) updates the stored interval and the arm instruction
    reconciles the running cron (`CronDelete` old `*/3` + `CronCreate` `*/5`)
  - `SessionStart` emits the re-arm context when the flag is on, nothing when off
  - the tick prompt carries the marker, its self-cancel branch triggers on `INACTIVE`, and its turn
    is subject to the existing Stop hook

## Deferred refinements (explicitly NOT in the minimal feature)

Each is a strict addition; none is needed for a working watchdog:

- **block-until-armed + heartbeat + bounded cap** — force arming via the Stop hook's block if
  injection proves unreliable in practice (bounded by the `max_nudges` pattern,
  `reviewer_stopcontinue.py:176`, so forcing can never wedge the agent).
- **`fail_open` / `authorized` / `blocked` decision markers + a watchdog check script** — let the
  tick *skip* real work unless the last stop was a genuine unsupervised fail-open, instead of
  taking a small turn every interval.
- **configurable opt-in alert** (default off) — a loud notification when auto-resume can't happen.
- **"harden"** — shorter `STOP_TIMEOUT` + in-budget retry so a *slow* supervisor still returns a
  verdict before failing open.

## Verified surface (read, not assumed)

| symbol | file:line | fact |
|---|---|---|
| fail-open rule | `stop_hook.py` `keep_going = …` | timeout/empty/nonzero ⇒ stop allowed |
| `STOP_TIMEOUT` | `stop_hook.py:35` | 300s; hook cap 330 in `hooks/hooks.json` |
| `has_outstanding_background_task` | `reviewer_manifest.py:829` | bg-task exclusion the Stop hook already honors |
| `max_nudges()` | `reviewer_stopcontinue.py:176` | the bound pattern for the deferred arming cap |
| SessionStart injects nothing to the agent today | `session_start_hook.py` | only briefs the reviewer's own session; `additionalContext` must be ADDED |
| toggle shape | `stopcontinue_toggle.py` | per-project flag + `acceptRisk`; the pattern to mirror |

## Limitations

- **Auto-resume is best-effort.** In the minimal feature no hook can force a tool call, so recovery
  depends on the model actually calling `CronCreate`. If it does not arm (or `CronCreate` is
  unavailable on the host), there is no recovery until the deferred *block-until-armed* refinement.
- **The `CronCreate` "local, idle-firing" assumption** still wants the isolated spike above.
- **Cron granularity = 1 minute** floor.
- **Ticks fire every interval while armed** — small turns, even when idle-but-done. The
  decision-marker refinement removes the no-op turns; until then the interval is the dial.
