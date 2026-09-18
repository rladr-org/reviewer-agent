# Watchdog: a phantom background-task id keeps the recovery cron alive forever

## Symptom

The `stopContinueWatchdog` recovery cron fires every interval and **never auto-shuts-off**, even
after the session is idle and its work is done — far more than the two or three idle ticks the
auto-shutoff is supposed to take. Every tick on the affected session logs:

```
[stop] ENTER ... bg_wait=True
[stop] ... background wait (harness auto-wakes on completion) -> allow stop, no supervisor call
```

This is **distinct** from the cosmetic "the tick shows a `Read` error on the normally-absent
teardown marker" item already in `BACKLOG.md`. That one is about appearance; this is a functional
never-teardown bug.

## Root cause

`has_outstanding_background_task()` (`reviewer_manifest.py`) reports a **completed / nonexistent**
background task as still outstanding, because `_BG_SHELL_STARTED`'s capture group is too loose:

```python
_BG_SHELL_STARTED = re.compile(r"Command running in background with ID:\s*([^\s.,]+)")
```

`[^\s.,]+` matches the pattern's **own source text** — and any transcript that quotes the
launch-ack phrasing (docs, a pasted bug report, or simply reading this source while debugging the
watchdog). Run over the text `Command running in background with ID:\s*([^\s.,]+)` it captures the
phantom id `\s*([^\s`. That phantom never receives a completion `<task-notification>`, so
`_ended_monitor_ids` never marks it ended, and the background-shell path suppresses stopContinue
for the 12-hour `_bg_shell_stale_secs()` window → `bg_wait=True`.

In `stop_hook.py::_main`, the `if bg_wait: return 0` early return (the "background wait" path)
precedes `_watchdog_on_stop_ok()` — the *only* place `.disarm` / `.teardown_ready` are written. So
the idle countdown never starts, `.teardown_ready` is never written, and the cron never reaches its
self-delete condition.

## Why the fix is in the detector, not the stop-hook ordering

`_watchdog_on_stop_ok` is deliberately skipped on the bg-wait / tool-only / nudge-cap paths to
"leave the watchdog armed to recover an incomplete or parked strand." Arming the countdown on the
bg-wait path would tear the watchdog down while a genuinely-parked strand still needs recovery,
violating that intent. With the detector corrected, `bg_wait` is `False` whenever nothing is
actually running, and the normal STOP_OK path arms and tears down the cron exactly as designed.

`_ASYNC_AGENT_STARTED` already uses the strict charset `([0-9A-Za-z_-]+)`, which is exactly why the
async-subagent path is NOT vulnerable to the same self-match. The shell pattern is the outlier.

## Evidence (detector run against a live transcript)

```
shell_ids: ['\\s*([^\\s', 'b59txmcta', 'beapnjft7', 'bf3pwj50j', 'bl2je21aj', ...]
  shell \s*([^\s : ended?=False  within_12h?=True   <- the sole suppressor
  (every real shell id: ended?=True)
job_state_live: set()   active_monitors: []   live_async_agents: []
HAS_OUTSTANDING: True
```

## Fix

1. Tighten the capture to the harness id grammar (mirrors the already-safe `_ASYNC_AGENT_STARTED`):
   ```python
   _BG_SHELL_STARTED = re.compile(r"Command running in background with ID:\s*([A-Za-z0-9_-]+)")
   ```
2. Defense in depth: in `_background_shell_task_ids`, drop any captured id that does not match
   `^[A-Za-z0-9_-]+$`, so every transcript-derived id — now and for any future pattern — is immune
   to this class of self-match / transcript poisoning.
3. Leave `stop_hook.py` and `_watchdog_on_stop_ok` unchanged.

## Verification (monkeypatch of the regex against the live transcript + controls)

- New regex captures a real ack id (`…ID: bl2je21aj…` → `bl2je21aj`) and returns `None` on the
  pattern's own text (both the old and the new pattern text).
- Real transcript: `has_outstanding_background_task` flips **`True` → `False`**; `shell_ids` drops
  the phantom and keeps all real ids.
- Positive control (fix applied, `_ended_monitor_ids` forced empty): still `True` — a genuine
  unended background shell is still detected, so the tightened regex did not over-correct.

## Status

Diagnosed, and the fix **verified via monkeypatch** against the live transcript. NOT yet done: the
change committed in `reviewer_manifest.py`, a TDD regression test added, the manifest suite run, or
the full live end-to-end teardown observed (fix in place → a couple of ticks → `.teardown_ready`
written → cron auto-deleted). The detector flip is the confirmed root cause; the downstream
`_watchdog_on_stop_ok` arming is existing, already-tested code.

## TDD

A regression test that builds a transcript containing (a) the literal `_BG_SHELL_STARTED` pattern
text and (b) one genuine **completed** launch ack, and asserts `has_outstanding_background_task(...)
is False` (no phantom, real completion honored); plus a unit test that `_background_shell_task_ids`
ignores a non-id capture.

## Scope

Claude-side only: `reviewer_manifest.py` and its tests. Not the Codex package.
