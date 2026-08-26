# `/stopContinueWatchdog` — recover an idle-stranded agent

## Why

stopContinue supervises **only at a turn boundary**: `stop_hook.py` runs when the agent ends its
turn. It is **fail-open** — `stop_hook.py` computes
`keep_going = code == 0 and bool(out.strip()) and not rl.is_stop_ok(out)`, so any supervisor
timeout, non-zero exit, or empty verdict lets the stop proceed. `STOP_TIMEOUT = 300` and the hook's
own `hooks.json` timeout is 330s.

That leaves an unrecovered failure mode: **the supervisor API times out (or the main model's
connection drops mid-response) → the hook fails open → the agent goes idle → nothing ever re-fires
the Stop hook.** The agent is stranded with no retry. Observed live 2026-08-25 (`Connection lost
mid-response`), and the exact shape the operator asked to close.

The whole point of stopContinue is that a genuinely-incomplete stop keeps working. An *unsupervised*
fail-open silently defeats that, and — unlike a nudge cap being hit — leaves no trace and no path back.

## The platform constraint (verified, not assumed)

There is **no supported way to wake an idle interactive TUI session reactively from outside.**
Confirmed three ways (Claude Code docs, the filesystem, and the daemon architecture):

| claim | status | source |
|---|---|---|
| No IPC / socket / signal / file-watch injects input into a running interactive session | DOCUMENTED | CLI + hooks + sessions docs |
| `claude --resume`/`--continue` spawn a NEW process against the transcript, not re-enter the live one | DOCUMENTED | sessions docs |
| A hook cannot force a tool call, enqueue a turn, or inject a prompt | DOCUMENTED | hooks reference |
| A bare OS process a hook spawns does NOT trigger re-invocation (only harness-tracked tasks do) | DOCUMENTED | tools reference |
| `Notification`/`idle_prompt` hook fires when the session goes idle but is **output-only** | DOCUMENTED | hooks reference |
| `tmux send-keys` is the only thing that types into the live pane — unsupported, host-specific | — | rejected as fragile |
| The scheduler is a daemon reached over a private key-gated control socket (`~/.claude/daemon`) | OBSERVED | filesystem |

The only **supported** re-invocation of an idle session is a **timer the session itself armed**:

| primitive | who starts it | local? | note |
|---|---|---|---|
| `/loop <interval> <prompt>` | **human only** — model cannot emit slash commands | local | fires during idle |
| `CronCreate(cron, prompt)` | **model** (tool call) | **local session task** | the arming primitive |
| `ScheduleWakeup(ms)` | model | — | only RE-paces an already-running dynamic `/loop`; cannot start one |
| `RemoteTrigger` | model | **cloud** | wrong — would not wake the local TUI |

**Conclusion:** recovering an idle session *requires* a timer armed while the agent was still active,
and `CronCreate` is the only model-callable primitive that provides it. This is a platform ceiling,
not a design choice.

> Open validation before implementation: the "CronCreate = local, idle-firing" claim is from a
> doc-based agent read. Run a ~2-minute spike in an **isolated** session (`CronCreate` a
> `*/1 * * * *` task; confirm it fires a turn while the session sits idle) before building on it.

## Mechanism

1. **Arm a `CronCreate` loop** (interval N min → `*/N * * * *`), prompt = a watchdog sentinel.
2. **Each fire** (a real model turn, even while idle) runs a watchdog check that decides
   continue-vs-nothing. Most fires are no-ops.
3. **Force arming with the Stop hook's block** (the same lever stopContinue already uses): if the
   flag is on and the loop is not provably armed, block the stop with "arm via `CronCreate(...)`
   now" — a **local** check needing no supervisor API. **Bounded by a cap** (reuse the
   `max_nudges` pattern, `reviewer_stopcontinue.py:176`): after N failed attempts, stop blocking
   and fall back to the opt-in alert. Forcing must never itself wedge the agent.
4. **Heartbeat marker**: each watchdog fire writes a "last fired" timestamp. The Stop hook treats
   the loop as alive only if the marker is fresh (`< ~2×interval`); missing/stale ⇒ re-force
   arming. This catches silent loop death (`/clear`, compaction). SessionStart clears it.

### The watchdog check (`reviewer_watchdog.py`, unit-testable)

The cron prompt tells the model to run this script; it:
1. refreshes the heartbeat marker;
2. if `has_outstanding_background_task(transcript)` is true → **no-op** (harness will wake it — the
   same exclusion `stop_hook.py` already honors, `reviewer_manifest.py:has_outstanding_background_task`);
3. reads the Stop hook's **last-decision marker** (`authorized` / `fail-open` / `blocked`);
4. if the last stop was an **unsupervised fail-open** and no bg task → **re-run supervision**:
   - keep-going ⇒ emit the directive; the model resumes the real task;
   - STOP_OK ⇒ record authorized, no-op;
   - still unreachable ⇒ no-op, **stay armed**, retry next interval;
5. otherwise no-op. A no-op **never disarms the loop** — only `authorized` STOP_OK or
   `/stopContinueWatchdog off` does.

## Command

`/stopContinueWatchdog on|off [interval]` — mirrors the existing toggle family
(`/stopContinue`, `stopcontinue_toggle.py`; `/reviewerModel … timeout`). `on` sets a per-project
flag + interval and, in the same (active) turn, has the model arm the cron and write the "armed"
marker — the highest-compliance arm moment.

## Verified surface (read, not assumed)

| symbol | file:line | fact |
|---|---|---|
| fail-open rule | `stop_hook.py` `keep_going = …` | timeout/empty/nonzero ⇒ stop allowed |
| `STOP_TIMEOUT` | `stop_hook.py:35` | 300s; hook cap 330 in `hooks/hooks.json` |
| `has_outstanding_background_task` | `reviewer_manifest.py:829` | the bg-task exclusion to reuse verbatim |
| `max_nudges()` | `reviewer_stopcontinue.py:176` | the bound pattern to copy for the arming cap |
| SessionStart injects nothing to the agent | `session_start_hook.py` | only briefs the reviewer's own session; `additionalContext` must be ADDED |
| toggle shape | `stopcontinue_toggle.py` | per-project flag + `acceptRisk`; the pattern to mirror |

## Files

- **new** `commands/stopContinueWatchdog.md` + `stopcontinue_watchdog_toggle.py`
- **new** `reviewer_watchdog.py` — check logic + marker I/O
- `reviewer_state.py` — flag/interval/alert accessors; last-decision + heartbeat markers
- `stop_hook.py` — write the last-decision marker on every decision; bounded block-until-armed (before the supervisor call)
- `session_start_hook.py` — inject `additionalContext` to (re)arm when the flag is on
- `reviewer_prompts.py` — arm directive + watchdog sentinel text
- `README.md` / `CHANGELOG.md`
- tests — toggle; decision-marker per outcome; block-until-armed (blocks unarmed / allows armed / cap→fallback / heartbeat-stale); watchdog_check (fail-open+back⇒continue, +down⇒no-op-stay-armed, authorized⇒no-op, bg-task⇒no-op, heartbeat refresh); SessionStart arm-injection

## Limitations (stated up front)

- **Auto-resume is best-effort.** Block-until-armed is bounded, so a session that genuinely cannot
  arm (or where `CronCreate` is unavailable) degrades to the **opt-in alert** (default off).
- **Arming is model-cooperative.** No hook can force a tool call; the block makes non-arming
  *unlikely and detectable*, not *impossible*. The only "forced" alternative is unsupported
  injection (tmux/daemon socket), rejected.
- **Cron granularity = 1 minute** floor.
- **The loop fires every interval while armed**, even when idle-but-done — intrusive on long
  sessions; the interval is the dial.
- **"Harden"** (shorter `STOP_TIMEOUT` + in-budget retry) stays a separate, optional change; added
  only if implementation needs it.
