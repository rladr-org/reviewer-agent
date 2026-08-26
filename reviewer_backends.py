#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Backends layer of the reviewer package: the two processes that actually run a review.

Step 7 of LIB_SPLIT_PLAN.md. Imports `base`, `prompts` and `state`;
`tools/check_split_layering.py` fails if it ever reaches forward in the layer order.

Everything that spawns a subprocess lives here and nowhere else: `_run` is the single point BOTH
backends execute through, codex argv construction and session resume, the briefing call that
mints a session id, and the hermetic pi backend. The line against `prompts` is whether a process
starts -- `compose_brief` is pure text and stays there, while `brief_and_capture_text` runs codex
and belongs here.

`_LAST_INVOCATION` is deliberately NOT re-exported by `reviewer_lib`. It is REBOUND under
`global` on every `_run`, so a facade alias would freeze at its initial value while this module
carried the real data; every consumer goes through `last_invocation()` instead
(LIB_SPLIT_PLAN.md 5.6).

Tests patch the seams here -- `_run`, `pi_argv`, `parse_pi_final_text`, `parse_pi_tool_flags`,
`brief_and_capture_text` -- on THIS module, not on `reviewer_lib`: the callers live here too, so
they resolve those names in this module's globals and a facade patch would not reach them
(LIB_SPLIT_PLAN.md 5.3).

Everything here is re-exported by `reviewer_lib` for back-compat, so existing
`rl.run_pi_review(...)` call sites are unchanged.
"""
import json
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from reviewer_base import (
    log,
    terminate_process_tree,
)
from reviewer_prompts import (
    compose_brief,
)
from reviewer_state import (
    DEFAULT_FIRST_OUTPUT_TIMEOUT,
    DEFAULT_IDLE_TIMEOUT,
    ROLE_REVIEWER,
    canonical_project_key,
    claude_bin,
    codex_bin,
    codex_effort,
    codex_effort_config,
    codex_model,
    codex_model_config,
    config_get,
    legacy_session_files,
    read_claude_session,
    read_pi_session,
    read_session_id,
    record_session_brief,
    reviewer_model,
    reviewer_effort,
    write_claude_session_if_absent,
    write_pi_session_if_absent,
    write_session_id_if_absent,
)
import uuid

DEFAULT_TIMEOUT = 600


# --------------------------------------------------------------------------
# AskUserQuestion serialization
# --------------------------------------------------------------------------
def serialize_questions(tool_input):
    questions = (tool_input or {}).get("questions", []) or []
    lines = []
    for i, q in enumerate(questions, 1):
        header = q.get("header", "")
        text = q.get("question", "")
        multi = " (multi-select)" if q.get("multiSelect") else ""
        lines.append("Q{} [{}]{}: {}".format(i, header, multi, text))
        for opt in q.get("options", []) or []:
            lines.append("  - {}: {}".format(opt.get("label", ""), opt.get("description", "")))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# codex invocation
# --------------------------------------------------------------------------
# Exit code `_run` reports for a timed-out reviewer call (the shell convention for
# SIGKILL-by-timeout). Named so callers can tell "the reviewer ran out of time" -- which has
# its own remedy, a bigger budget -- from "the reviewer failed".
TIMEOUT_EXIT = 124


# What the most recent `_run` actually executed. Recorded HERE because `_run` is the single
# point BOTH backends execute through: capturing argv at the two call sites instead would mean
# rebuilding it, and the rebuilt copy is what would silently drift from the real one. It also
# means a knob added later is captured without anyone remembering to extend an audit schema.
_LAST_INVOCATION = None


def reset_last_invocation():
    """Clear the recorder immediately before a call whose cost is about to be attributed. The
    brief and the audit are two separate `_run` calls, and without this the audit would inherit
    the brief's argv and duration."""
    global _LAST_INVOCATION
    _LAST_INVOCATION = None


def last_invocation():
    """`{"argv": [...], "dur_s": float}` for the most recent `_run`, or None."""
    return _LAST_INVOCATION


def _record_invocation(argv, started, t_first_event_s=None, max_gap_s=None):
    global _LAST_INVOCATION
    _LAST_INVOCATION = {
        "argv": [str(a) for a in argv],
        "dur_s": round(time.monotonic() - started, 3),
    }
    # Streaming timing, recorded only when _run_streaming supplied it, so a plain _run record is
    # unchanged. t_first_event_s stays None on an N1 kill (no output ever arrived).
    if t_first_event_s is not None:
        _LAST_INVOCATION["t_first_event_s"] = round(t_first_event_s, 3)
    if max_gap_s is not None:
        _LAST_INVOCATION["max_gap_s"] = round(max_gap_s, 3)


# Signals a host sends to cancel an in-flight hook. Escape in Claude Code arrives as one of
# these; SIGHUP arrives when the terminal itself goes away.
_CANCEL_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)


_GUARD = str(Path(__file__).resolve().with_name("reviewer_process_guard.py"))


def _terminate(process):
    """Kill `process` and everything it started, then reap it."""
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            terminate_process_tree(process.pid, include_root=True)
        else:
            process.terminate()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass


def _run(argv, input_bytes=None, timeout=DEFAULT_TIMEOUT, cwd=None, on_cancel=None):
    """Run one reviewer call, and make sure nothing it started outlives it.

    Three cleanup paths, because a review ends early in three different ways:

    TIMEOUT   `subprocess.run(timeout=...)` killed the direct child only, so a codex audit that
              had started the project's test suite left those processes running -- under
              `workspace-write`, against the working tree. The whole descendant tree is
              terminated instead, groups included, since a test runner may have made its own.

    CANCEL    the host signals the hook on Escape. A handler is installed for the duration of
              the call so the reviewer tree is torn down before this process exits; the previous
              handlers are restored afterwards, so nothing outside this call is affected. A
              caller that passes `on_cancel` gets one call to record the outcome, between the
              teardown and the re-raise -- the last moment this process is still alive.

    PARENT DEATH  a SIGKILLed hook runs no handler at all. The reviewer therefore runs under
              `reviewer_process_guard.py`, which holds the read end of a pipe this process keeps
              open; the EOF that follows our death is what tells the guard to clean up.

    cwd matters for pi (which has no `-C` flag and audits its process cwd); codex uses
    `-C proj` and leaves cwd=None (inherit), so its behavior is unchanged.
    """
    started = time.monotonic()
    posix = os.name == "posix"
    lifecycle_read = lifecycle_write = None
    launch_argv = list(argv)
    popen_kwargs = {}
    if posix:
        lifecycle_read, lifecycle_write = os.pipe()
        launch_argv = [sys.executable, _GUARD, str(lifecycle_read)] + list(argv)
        popen_kwargs["pass_fds"] = (lifecycle_read,)
        # Its own session, so a group-wide signal aimed at the host's children cannot reach the
        # reviewer before this process has decided what to do about it.
        popen_kwargs["start_new_session"] = True
    if cwd is not None:
        popen_kwargs["cwd"] = cwd

    def _close(descriptor):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    try:
        process = subprocess.Popen(
            launch_argv,
            stdin=subprocess.DEVNULL if input_bytes is None else subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs
        )
    except FileNotFoundError as e:
        _close(lifecycle_read)
        _close(lifecycle_write)
        # Recorded on the failure paths too: a timeout and a missing binary are outcomes worth
        # measuring, and a record that only ever describes successes measures the wrong thing.
        _record_invocation(argv, started)
        return 127, "", "reviewer agent binary not found: {}".format(e)
    except OSError as e:
        _close(lifecycle_read)
        _close(lifecycle_write)
        _record_invocation(argv, started)
        return 127, "", "could not start the reviewer: {}".format(e)
    _close(lifecycle_read)  # only the guard needs the read end

    previous_handlers = {}
    if posix:
        def _cancelled(signal_number, _frame):
            _terminate(process)
            if on_cancel is not None:
                try:
                    on_cancel()
                except Exception:
                    # Recording is instrumentation; it must not stop the teardown from
                    # finishing or change the exit status the host sees.
                    pass
            # Re-raise with the default disposition so the exit status still says "signalled",
            # which is what the host and any wrapping shell expect to see.
            signal.signal(signal_number, signal.SIG_DFL)
            os.kill(os.getpid(), signal_number)

        for number in _CANCEL_SIGNALS:
            try:
                previous_handlers[number] = signal.signal(number, _cancelled)
            except (OSError, ValueError):
                # Not the main thread, or the platform refuses this signal: the guard and the
                # timeout path still cover the reviewer.
                pass

    try:
        try:
            stdout, stderr = process.communicate(input=input_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process)
            stdout, stderr = process.communicate()
            _record_invocation(argv, started)
            out = (stdout or b"").decode("utf-8", "replace")
            return TIMEOUT_EXIT, out, "review timed out after {}s".format(timeout)
        except BaseException:
            # KeyboardInterrupt, or anything else that unwinds this call: never leave the
            # reviewer running behind it.
            _terminate(process)
            raise
    finally:
        for number, handler in previous_handlers.items():
            try:
                signal.signal(number, handler)
            except (OSError, ValueError):
                pass
        _close(lifecycle_write)

    _record_invocation(argv, started)
    return (
        process.returncode,
        (stdout or b"").decode("utf-8", "replace"),
        (stderr or b"").decode("utf-8", "replace"),
    )


def _run_streaming(argv, timeout=DEFAULT_TIMEOUT, cwd=None, on_cancel=None,
                   first_output_timeout=None, idle_timeout=None):
    """Run a codex `--json` audit while WATCHING its event pipe, so a hung reviewer is killed by
    an idle threshold instead of only the total cap. Same process lifecycle as `_run` (guard,
    signal handlers, tree teardown); the difference is a `select()` read loop with THREE deadline
    checks per poll, in this order so a genuinely slow-but-alive audit is never misread as idle:

      1. TOTAL      now-start >= `timeout` -> reason "review timed out after Ns" (NO "idle"
                    substring, so run_codex_streaming_audit does NOT retry it).
      2. N1 first   before any byte, now-start >= `first_output_timeout` -> "idle timeout: no
                    output within Ns". Catches "codex launched but the API never responded".
      3. N2 between after the first byte, now-last_activity >= `idle_timeout` -> "idle timeout:
                    no output for Ns". Must exceed the longest legitimate silent item (a test run).

    Any byte on stdout OR stderr resets the idle clock. `--json` emits only at item boundaries,
    so N2 must be large; N1 can be small because `thread.started` lands ~1s in. Returns
    `(returncode, stdout, stderr)`; on a kill, `(TIMEOUT_EXIT, partial_stdout, reason)`.

    select() on pipes is POSIX-only, so on non-POSIX this delegates to `_run` (total cap only, no
    idle detection) -- Windows keeps working, just without the two thresholds.
    """
    if first_output_timeout is None:
        first_output_timeout = DEFAULT_FIRST_OUTPUT_TIMEOUT
    if idle_timeout is None:
        idle_timeout = DEFAULT_IDLE_TIMEOUT
    if os.name != "posix":
        return _run(argv, timeout=timeout, cwd=cwd, on_cancel=on_cancel)

    started = time.monotonic()
    lifecycle_read, lifecycle_write = os.pipe()
    launch_argv = [sys.executable, _GUARD, str(lifecycle_read)] + list(argv)
    popen_kwargs = {"pass_fds": (lifecycle_read,), "start_new_session": True}
    if cwd is not None:
        popen_kwargs["cwd"] = cwd

    def _close(descriptor):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    try:
        process = subprocess.Popen(
            launch_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **popen_kwargs
        )
    except FileNotFoundError as e:
        _close(lifecycle_read)
        _close(lifecycle_write)
        _record_invocation(argv, started)
        return 127, "", "reviewer agent binary not found: {}".format(e)
    except OSError as e:
        _close(lifecycle_read)
        _close(lifecycle_write)
        _record_invocation(argv, started)
        return 127, "", "could not start the reviewer: {}".format(e)
    _close(lifecycle_read)  # only the guard needs the read end

    previous_handlers = {}

    def _cancelled(signal_number, _frame):
        _terminate(process)
        if on_cancel is not None:
            try:
                on_cancel()
            except Exception:
                pass
        signal.signal(signal_number, signal.SIG_DFL)
        os.kill(os.getpid(), signal_number)

    for number in _CANCEL_SIGNALS:
        try:
            previous_handlers[number] = signal.signal(number, _cancelled)
        except (OSError, ValueError):
            pass

    out = bytearray()
    err = bytearray()
    fd_out, fd_err = process.stdout.fileno(), process.stderr.fileno()
    buffers = {fd_out: out, fd_err: err}
    open_fds = {fd_out, fd_err}
    for fd in open_fds:
        os.set_blocking(fd, False)  # so a drain read never blocks on a pipe a grandchild holds

    def _drain(fd):
        """Read all bytes IMMEDIATELY available on `fd` (non-blocking) into its buffer; return
        True at EOF. Never blocks: BlockingIOError just means nothing is ready right now."""
        while True:
            try:
                chunk = os.read(fd, 65536)
            except (BlockingIOError, OSError):
                return False
            if not chunk:
                return True  # EOF
            buffers[fd].extend(chunk)

    last_activity = started
    first_seen = False
    t_first_event = None
    max_gap = 0.0
    kill_reason = None

    try:
        try:
            while open_fds:
                now = time.monotonic()
                # Deadline checks, TOTAL first so a slow-but-alive audit is never called idle.
                if now - started >= timeout:
                    kill_reason = "review timed out after {}s".format(timeout)
                    break
                if not first_seen and now - started >= first_output_timeout:
                    kill_reason = "idle timeout: no output within {}s".format(first_output_timeout)
                    break
                if first_seen and now - last_activity >= idle_timeout:
                    kill_reason = "idle timeout: no output for {}s".format(idle_timeout)
                    break
                # Wake at the NEAREST deadline so none is overshot.
                if first_seen:
                    next_idle = last_activity + idle_timeout
                else:
                    next_idle = started + first_output_timeout
                wait = max(0.0, min(started + timeout, next_idle) - now)
                readable, _, _ = select.select(list(open_fds), [], [], wait)
                if not readable:
                    continue  # nothing arrived; re-check the deadlines at the top
                for fd in readable:
                    try:
                        chunk = os.read(fd, 65536)
                    except (BlockingIOError, OSError):
                        continue
                    if not chunk:
                        open_fds.discard(fd)  # EOF on this pipe
                        continue
                    now = time.monotonic()
                    if not first_seen:
                        first_seen = True
                        t_first_event = now - started
                    else:
                        # Only BETWEEN-output gaps feed max_gap; the start->first gap is
                        # t_first_event (what N1, not N2, governs).
                        max_gap = max(max_gap, now - last_activity)
                    last_activity = now
                    buffers[fd].extend(chunk)
                # A completed review closes its pipes; drain any last bytes so we return promptly
                # instead of spinning to the total cap (select reports a closed pipe as readable).
                if process.poll() is not None:
                    for fd in list(open_fds):
                        if _drain(fd):
                            open_fds.discard(fd)
                    break
        except BaseException:
            _terminate(process)
            raise
    finally:
        for number, handler in previous_handlers.items():
            try:
                signal.signal(number, handler)
            except (OSError, ValueError):
                pass
        _close(lifecycle_write)

    if kill_reason is not None:
        _terminate(process)
        for fd in (fd_out, fd_err):
            _drain(fd)  # keep whatever partial output was already emitted
        _close_streams(process)
        _record_invocation(argv, started, t_first_event_s=t_first_event, max_gap_s=max_gap)
        return TIMEOUT_EXIT, out.decode("utf-8", "replace"), kill_reason

    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _terminate(process)  # pipes closed but the child somehow lingers
    _close_streams(process)
    _record_invocation(argv, started, t_first_event_s=t_first_event, max_gap_s=max_gap)
    return (
        process.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _close_streams(process):
    for stream in (process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except OSError:
            pass


# `codex exec` is non-interactive and has NO --ask-for-approval flag; the approval
# policy is set via a config override instead. `approval_policy="never"` keeps the
# sandbox (-s) in force while never pausing for a prompt there is no human to answer.
APPROVAL_CONFIG = 'approval_policy="never"'


# Buy latency with quota. EVERY reviewer call blocks someone: a commit sitting at a
# fail-closed gate, an AskUserQuestion the agent cannot proceed past, a stop check. codex's
# own model catalog (~/.codex/models_cache.json) describes this tier as "Fast -- 1.5x speed,
# increased usage", so the trade is explicit and it is the right one here.
#
# Verified live against codex-cli 0.142.3 rather than assumed, because `codex doctor` accepts
# a bogus tier without complaint -- the field is a free-form string at config load, so a
# config-level check proves nothing. Under `RUST_LOG=info` the SessionConfiguredEvent reports
# `service_tier: Some("priority")` on BOTH the fresh `exec` path and the `resume <id>` path
# the audits actually use. An unrecognised value was likewise accepted without error, so a
# model whose catalog entry advertises no tiers (gpt-5.4-mini and gpt-5.3-codex-spark list
# none) degrades quietly instead of failing every commit at the fail-closed gate.
#
# The user's ~/.codex/config.toml sets service_tier globally; this overrides it for reviewer
# calls only, and callers can still override in turn via extra_config (see resume_by_id_argv).
SERVICE_TIER_CONFIG = 'service_tier="priority"'


# The reviewer's codex sessions must not load plugins. The reviewer-agent-codex plugin
# registers an MCP stop_continue tool globally; when that tool is available inside a commit
# audit session and stopContinue is active, the model sees STOP_OK vocabulary from the tool
# response and emits it as its verdict instead of APPROVED/REJECTED.
DISABLE_PLUGINS = 'features.plugins=false'


# The brief only needs to CREATE the reviewer session (and seed it with reviewerAgent.md);
# its response text is discarded. So run it at low reasoning effort -- the session id
# (`thread.started`) is emitted up front regardless, and this keeps session start fast even
# when the user's global model_reasoning_effort is xhigh. The AUDIT (resume) does NOT set
# this, so it inherits the user's configured effort.
BRIEF_REASONING_CONFIG = 'model_reasoning_effort="low"'


# Upper bound on waiting for the brief to yield a session id. The id arrives in the first
# stdout event (~1s); this is only a safety cap, decoupled from the much longer AUDIT
# timeouts so a slow brief never blocks session start / a commit for minutes.
BRIEF_CAPTURE_TIMEOUT = 90


def brief_argv(proj):
    argv = [
        codex_bin(), "exec", "-C", str(proj),
        "--sandbox", "read-only", "-c", APPROVAL_CONFIG,
        "-c", SERVICE_TIER_CONFIG, "-c", DISABLE_PLUGINS,
    ]
    model = codex_model()
    if model:
        argv += ["-c", codex_model_config(model)]
    return argv + ["-"]


def run_codex_brief(proj, brief_path, timeout=DEFAULT_TIMEOUT):
    with open(brief_path, "rb") as fh:
        data = fh.read()
    return _run(brief_argv(proj), input_bytes=data, timeout=timeout)


def parse_session_id(stdout):
    """Extract the codex session/thread id from `codex exec --json` output. The REAL
    stdout event stream begins with a `thread.started` event carrying `thread_id` (this
    is what `codex resume <id>` accepts); the on-disk rollout instead uses
    `session_meta`/`session_id`. Accept either form (and a bare top-level id) and return
    the first id found, or None. Verified against codex CLI 0.142.x live output."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        sid = (
            ev.get("thread_id")
            or ev.get("session_id")
            or payload.get("session_id")
            or payload.get("thread_id")
        )
        if sid:
            return sid
    return None


# --------------------------------------------------------------------------
# Strict codex verdict schema (docs/plans/codex_jsonl_streaming.md, Task 1).
#
# The codex commit audit runs with `--output-schema codex_verdict_schema.json -o <tmp>`, so
# codex writes a schema-constrained JSON object to the temp file. We parse that STRICTLY and
# reconstruct the canonical APPROVED/REJECTED text, keeping commit_hook.is_approved and
# reviewer_audit backend-agnostic and unchanged. Everything here fails CLOSED (-> None / "").
# --------------------------------------------------------------------------
_VERDICT_ENUM = ("APPROVED", "REJECTED")
_VERDICT_KEYS = frozenset({"verdict", "findings", "suggestions"})


def codex_verdict_schema_path():
    """Absolute path to the shipped strict-verdict JSON Schema, resolved where THIS module
    lives so the deployed tree uses its own copy, not the dev checkout's."""
    return str(Path(__file__).resolve().parent / "codex_verdict_schema.json")


def _all_strings(seq):
    return isinstance(seq, list) and all(isinstance(x, str) for x in seq)


def _strict_verdict_dict(obj):
    """Return `obj` iff it is EXACTLY a well-formed, self-consistent verdict dict, else None.
    The single source of truth for verdict validity, shared by parse_codex_verdict_json (after
    json.loads) and codex_verdict_to_text so BOTH fail closed identically. Strict: exactly the
    three keys {verdict, findings, suggestions}; `verdict` in the enum; `findings`/`suggestions`
    lists of strings; and the prompt-contract CONSISTENCY -- APPROVED has no findings, REJECTED
    has >=1. Without this, codex_verdict_to_text would reconstruct an inconsistent object (e.g.
    APPROVED with findings) into a bare `APPROVED`, silently dropping the findings and approving
    what should have been refused."""
    if not isinstance(obj, dict) or set(obj.keys()) != _VERDICT_KEYS:
        return None
    verdict = obj.get("verdict")
    findings = obj.get("findings")
    suggestions = obj.get("suggestions")
    if verdict not in _VERDICT_ENUM:
        return None
    if not _all_strings(findings) or not _all_strings(suggestions):
        return None
    # verdict/findings consistency: an approval lists no required fixes; a rejection must list at
    # least one. The contradictory combos fail closed rather than being silently accepted.
    if verdict == "APPROVED" and findings:
        return None
    if verdict == "REJECTED" and not findings:
        return None
    return {"verdict": verdict, "findings": findings, "suggestions": suggestions}


def parse_codex_verdict_json(text):
    """Parse the `-o` verdict file's single JSON object into {verdict, findings, suggestions},
    or None on ANY deviation (see _strict_verdict_dict). Never raises, never partially accepts."""
    if not isinstance(text, str):
        return None
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        return None
    return _strict_verdict_dict(obj)


def _numbered(items):
    return "\n".join("{}. {}".format(i, s) for i, s in enumerate(items, 1))


def codex_verdict_to_text(obj):
    """Reconstruct the canonical verdict text from a parsed verdict dict, byte-matching what
    is_approved / reviewer_audit and the developer expect: `APPROVED` (+ optional SUGGESTIONS
    block) or `REJECTED:` + numbered findings (+ optional SUGGESTIONS block). "" when `obj` is
    None or not a well-formed, self-consistent verdict dict -- it is re-validated through the same
    strict check as parsing, so a malformed or contradictory object fails closed to "" instead of
    becoming reviewer prose."""
    obj = _strict_verdict_dict(obj)
    if obj is None:
        return ""
    verdict, findings, suggestions = obj["verdict"], obj["findings"], obj["suggestions"]
    head = "APPROVED" if verdict == "APPROVED" else "REJECTED:\n" + _numbered(findings)
    if suggestions:
        return head + "\nSUGGESTIONS:\n" + _numbered(suggestions)
    return head


def parse_codex_turn_usage(stdout):
    """The `usage` dict from the LAST `turn.completed` event in a `codex exec --json` stream, or
    None if absent. Accepts usage at the event top level or under `payload` (mirrors
    parse_session_id's tolerance). Non-dict usage values are ignored. Never raises."""
    found = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("type") != "turn.completed":
            continue
        payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
        usage = ev.get("usage")
        if not isinstance(usage, dict):
            usage = payload.get("usage")
        if isinstance(usage, dict):
            found = usage
    return found


def brief_capture_argv(proj):
    """Brief argv that emits JSONL (`--json`) so the new session's id can be captured from
    the leading `thread.started` event. Read-only; low reasoning effort (session start
    only); reads the brief from stdin (`-`)."""
    argv = [
        codex_bin(), "exec", "-C", str(proj), "--json",
        "--sandbox", "read-only",
        "-c", APPROVAL_CONFIG,
        "-c", BRIEF_REASONING_CONFIG,
        "-c", SERVICE_TIER_CONFIG,
        "-c", DISABLE_PLUGINS,
    ]
    model = codex_model()
    if model:
        argv += ["-c", codex_model_config(model)]
    return argv + ["-"]


def _brief_capture_run(proj, data, timeout, on_cancel=None):
    """Run the read-only --json brief with `data` on stdin and capture the session id from
    the leading `thread.started` event (emitted as soon as the session exists, so it is
    parsed even if the turn then times out or exits nonzero). Returns (sid, code, out, err)."""
    code, out, err = _run(
        brief_capture_argv(proj), input_bytes=data, timeout=timeout, on_cancel=on_cancel
    )
    return parse_session_id(out), code, out, err


# NOTE: there is deliberately no brief-from-FILE helper. One existed (it piped
# reviewerAgent.md's raw bytes) and is removed rather than left unused: sending the project file
# alone silently omits REVIEWER_STANDING_BRIEF, so keeping it would leave a callable path that
# bypasses composition. Every brief goes through compose_brief -> brief_and_capture_text.
def brief_and_capture_text(proj, text, timeout=BRIEF_CAPTURE_TIMEOUT, on_cancel=None):
    """Brief from an in-memory string (compose_brief's output) and capture the session id."""
    return _brief_capture_run(
        proj, (text or "").encode("utf-8"), timeout, on_cancel=on_cancel
    )


# NOTE: there is deliberately no session-less resume helper. `codex resume --last` attaches
# to whatever codex session ran most recently on the MACHINE -- which may belong to another
# Claude session, or to the operator's own codex use -- so a reviewer built on it can silently
# inherit an unrelated thread. Every audit resumes BY ID via resume_by_id_argv below.
# (Moved here with the code at split step 8; tests/test_session_isolation.py:83 enforces it by
# asserting the facade has no `resume_argv` / `run_codex_resume`.)
def resume_by_id_argv(session_id, proj, sandbox, prompt, extra_config=None):
    """Resume a SPECIFIC codex session by id. Unlike `resume --last`, an explicit id is
    NOT cwd-filtered, so one briefed reviewer session serves the main checkout and every
    worktree; `-C proj` still sets the directory the audit runs in, so pointing -C at a
    worktree makes the same session audit that worktree's own staged diff."""
    argv = [codex_bin(), "exec", "-C", str(proj)]
    if sandbox:
        argv += ["--sandbox", sandbox]
    argv += ["-c", APPROVAL_CONFIG, "-c", SERVICE_TIER_CONFIG, "-c", DISABLE_PLUGINS]
    # The /reviewerModel override must reach THIS path: commit/askq/stop all resume by id,
    # so without it a mid-session model change would never take effect (the override would
    # apply only to session creation via the brief). Placed before extra_config and the
    # `resume` subcommand so a per-turn `-c model="..."` wins.
    model = codex_model()
    if model:
        argv += ["-c", codex_model_config(model)]
    # Effort reaches the audit and the askq answer, and ONLY them: the brief keeps its own
    # low (its response is discarded) and the stop supervisor keeps its own via extra_config.
    # Placed BEFORE extra_config for the same reason as the model -- codex resolves a repeated
    # `-c` key last-wins, so the supervisor's per-turn low must be appended after this to win.
    effort = codex_effort()
    if effort:
        argv += ["-c", codex_effort_config(effort)]
    for cfg in extra_config or []:
        argv += ["-c", cfg]
    argv += ["resume", str(session_id), prompt]
    return argv


def run_codex_resume_by_id(
    session_id, proj, sandbox, prompt, timeout=DEFAULT_TIMEOUT, extra_config=None,
    on_cancel=None,
):
    return _run(
        resume_by_id_argv(session_id, proj, sandbox, prompt, extra_config=extra_config),
        timeout=timeout,
        on_cancel=on_cancel,
    )


# --------------------------------------------------------------------------
# pi backend invocation. The reviewer runs pi HERMETICALLY READ-ONLY: only the
# read/search built-ins plus the custom `git_read` tool (a tested, no-shell,
# read-only git wrapper). NO bash/write/edit (excluded from -t), NO `-e` (the
# git-read-only extension is installed GLOBALLY and auto-discovers), and NO
# --no-extensions -- which would drop git-read-only itself, since extension
# discovery is how it loads.
#
# The read-only guarantee rests on `-t` ALONE, which pi documents as an allowlist
# applying to "built-in, extension, and custom tools". It does NOT depend on any
# third-party permission extension: an earlier version of this comment credited
# --no-extensions to preserving `@gotgenes/pi-permission-system`, which this
# plugin neither installs, requires, nor documents. Read that way, the guarantee
# would rest on something no install step guarantees is present.
# `-na` ignores the reviewed repo's own .pi resources; pi has no `-C`, so we spawn
# it with cwd=<repo> so git_read sees the right tree. Session continuation follows
# the CAPTURE pattern: `--mode json` prints a `{"type":"session","id":...}` header on
# the first turn; we capture + persist that id and resume later turns with `--session`.
# --------------------------------------------------------------------------
PI_READ_ONLY_TOOLS = "read,grep,find,ls,git_read"


def pi_bin():
    """Path to the pi binary. REVIEWER_PI_BIN wins; otherwise resolve `pi` on PATH
    (falling back to the literal `pi`). Resolving absolutely matters because the hook
    subprocess may not inherit the interactive shell's PATH (e.g. nvm's bin dir)."""
    env = os.environ.get("REVIEWER_PI_BIN") or config_get("piBin")
    if env:
        return env
    return shutil.which("pi") or "pi"


def pi_argv(prompt, session_id=None, model=None, thinking=None, system_prompt=None):
    """Argv for a read-only pi reviewer turn (`--mode json` so the session header is
    emitted for capture, and the final verdict is read from the event stream). Pass
    `session_id` to RESUME an existing session (`--session`); omit it on the first turn
    and supply `system_prompt` (the brief, `@<path>` or literal). The prompt is last."""
    # -nc: do NOT auto-load CLAUDE.md/AGENTS.md as context files -- the reviewer brief
    # (--append-system-prompt) already carries the standards, and auto-loaded context
    # bloats every turn and can bait exploration off into unrelated repo docs.
    argv = [pi_bin(), "--mode", "json", "-na", "-nc", "-t", PI_READ_ONLY_TOOLS]
    if session_id:
        argv += ["--session", str(session_id)]
    if model:
        argv += ["--model", model]
    if thinking:
        argv += ["--thinking", thinking]
    if system_prompt:
        argv += ["--append-system-prompt", system_prompt]
    argv += [prompt]
    return argv


def parse_pi_session_id(stdout):
    """The pi session UUID from the leading `{"type":"session","id":...}` header of a
    `--mode json` stream, or None."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "session" and ev.get("id"):
            return ev["id"]
    return None


def _pi_message_text(msg):
    content = msg.get("content") if isinstance(msg, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                parts.append(b["text"])
            elif isinstance(b, str):
                parts.append(b)
        return "".join(parts)
    return ""


def parse_pi_final_text(stdout):
    """Final assistant text from a `pi --mode json` event stream -- the text of the LAST
    assistant message (`message_end`; `agent_end.messages` as a fallback). '' if none."""
    final = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        msg = None
        if t == "message_end":
            msg = ev.get("message")
        elif t == "agent_end" and isinstance(ev.get("messages"), list):
            for m in ev["messages"]:
                if isinstance(m, dict) and m.get("role") == "assistant":
                    msg = m
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            txt = _pi_message_text(msg)
            if txt.strip():
                final = txt
    return final.strip()


def parse_pi_tool_flags(stdout):
    """Gate-safety signals from pi's event stream, which a prose verdict cannot be trusted over:

    - `truncated`  -- a git_read result hit its output cap, so the reviewer saw a PARTIAL patch;
    - `tool_error` -- a GIT_READ call errored, so it could not read the repo;
    - `saw_staged_read` -- a git_read for the STAGED DIFF completed without error.

    All three exist because the reviewer's verdict is only as good as what it actually read.

    `tool_error` is scoped to git_read, matching what this docstring always claimed; the code
    used to set it on ANY tool's error. That over-match is not hypothetical: the operator's
    pi-permission-system config allows read/write/edit/git_read and falls back to `"*": "ask"`,
    and in `--mode json` there is no UI to answer an ask, so a `grep` call returns isError
    (fast -- it denies rather than hanging). A good approval was then overridden with "A
    git_read call ERRORED", blocking the commit and naming a cause that never occurred.

    `saw_staged_read` covers the opposite and more dangerous failure: with the git-read-only
    extension ABSENT, `-t` enables a name with no tool behind it, so no call is made and NO
    error is raised. Nothing in the stream is wrong -- the evidence is simply missing, which is
    why silence has to be read as a signal rather than as consent. Start and end events are
    paired by `toolCallId` so a bare end event cannot satisfy it.

    Event shapes are as captured from real pi (2026-08-02), not inferred: `tool_execution_start`
    carries `toolName` and `args`, `tool_execution_end` carries `toolName` and `isError`."""
    truncated = False
    tool_error = False
    staged_calls = set()
    completed_staged = set()
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        if kind == "tool_execution_start":
            args = ev.get("args")
            if (ev.get("toolName") == "git_read" and isinstance(args, dict)
                    and args.get("subcommand") == "diff" and args.get("staged")):
                staged_calls.add(ev.get("toolCallId"))
            continue
        if kind != "tool_execution_end":
            continue
        is_git_read = ev.get("toolName") == "git_read"
        if ev.get("isError"):
            if is_git_read:
                tool_error = True
        elif ev.get("toolCallId") in staged_calls:
            completed_staged.add(ev.get("toolCallId"))
        result = ev.get("result")
        details = result.get("details") if isinstance(result, dict) else None
        if is_git_read and isinstance(details, dict) and details.get("truncated"):
            truncated = True
    return {"truncated": truncated, "tool_error": tool_error,
            "saw_staged_read": bool(completed_staged)}


def run_pi_review(
    cwd,
    claude_session_id,
    prompt,
    timeout=DEFAULT_TIMEOUT,
    thinking=None,
    role=ROLE_REVIEWER,
    require_staged_read=True,
    on_cancel=None,
):
    """One read-only Pi turn against ``cwd`` using capture-then-resume.

    Sessions are isolated by ``role``. The first turn receives the composed project brief,
    captures the Pi session id from the JSON header, and persists it in that role's slot;
    later turns resume that exact id without re-briefing.

    ``require_staged_read`` is true by default because the historical and primary caller is
    the fail-closed commit gate. Question answering and stop supervision set it false: those
    turns have no staged set, so applying the commit-only ``git_read --staged`` requirement
    would replace every valid answer with a synthetic rejection.
    """
    model = reviewer_model("pi")
    stored = read_pi_session(cwd, claude_session_id, role)
    if stored:
        argv = pi_argv(prompt, session_id=stored, model=model, thinking=thinking)
    else:
        # Literal composed text, not `@<path>`: an @-reference can only carry the project file,
        # so it had no way to deliver REVIEWER_STANDING_BRIEF. --append-system-prompt already
        # accepts literal text (DEFAULT_BRIEF was passed that way when no project brief existed).
        argv = pi_argv(prompt, model=model, thinking=thinking,
                       system_prompt=compose_brief(cwd))
    code, out, err = _run(argv, timeout=timeout, cwd=str(cwd), on_cancel=on_cancel)
    if not stored:
        sid = parse_pi_session_id(out)
        if sid:
            write_pi_session_if_absent(cwd, claude_session_id, sid, role)
    final = parse_pi_final_text(out)
    # Safety-critical for the gate: a truncated staged patch or a git_read error means the
    # reviewer did NOT see the full change, so OVERRIDE any APPROVED verdict with a REJECT
    # (is_approved -> False -> the caller denies). Programmatic, not string-matched.
    flags = parse_pi_tool_flags(out)
    if require_staged_read and flags["truncated"]:
        final = (
            "REJECTED:\n1. The staged diff was TRUNCATED (git_read hit its output cap), so the "
            "reviewer saw only a PARTIAL patch. A commit must not be gated on a partial view -- "
            "split the commit into smaller staged sets, or review each file's staged hunk."
        )
    elif require_staged_read and flags["tool_error"]:
        final = (
            "REJECTED:\n1. A git_read call ERRORED, so the reviewer could not read the staged "
            "diff. Cannot gate the commit (fail-closed); investigate the git_read failure."
        )
    # `.get` with a False default, not `flags[...]`: a caller or test double that predates this
    # signal must fail CLOSED rather than KeyError or silently approve. Absent evidence that the
    # staged patch was read is exactly the state this branch exists to refuse.
    elif require_staged_read and not flags.get("saw_staged_read", False):
        final = (
            "REJECTED:\n1. The reviewer never read the staged diff: no successful git_read for "
            "the staged patch appears in this session. Its verdict was formed without the "
            "change under review, so it cannot gate the commit (fail-closed). Usually the "
            "git-read-only extension is missing from ~/.pi/agent/extensions/ -- `-t` enables "
            "tool NAMES, so an absent tool raises no error and the reviewer simply proceeds "
            "without it. See the pi prerequisites in README.md."
        )
    return code, final, err


def retire_legacy_sessions(claude_session_id):
    """Discard every UNSCOPED session file for this Claude session.

    Two generations qualify: the pre-role-split `sess_<sid>.codex`, and the role-split
    `sess_<sid>.<role>.{codex,pi}` files that carried no project. Neither records which
    repository briefed the thread, so neither can be adopted into a project-scoped slot without
    guessing -- and a wrong guess hands a reviewer another repository's brief, findings and
    directives. Retiring them costs one fresh brief per project and is the only safe direction.

    Never raises: this is housekeeping, and a fail-closed gate must not be blocked by it."""
    for f in legacy_session_files(claude_session_id):
        try:
            if not f.exists():
                continue
            f.unlink()
        except OSError:
            continue
        log("[session] retired unscoped session file {}".format(f))


def ensure_session(cwd, claude_session_id, timeout=BRIEF_CAPTURE_TIMEOUT, role=ROLE_REVIEWER,
                   on_cancel=None):
    """Return a codex reviewer session id for THIS Claude session and `role`, briefing+capturing
    one if none is persisted yet. The brief runs at the CANONICAL PROJECT ROOT (where
    reviewerAgent.md lives and which is identical across worktrees); later audits resume
    this id with `-C <cwd>` so they inspect the right working tree. Success is keyed on a
    captured session id (the brief turn need not complete). Returns (sid, "") on success,
    or (None, error_message) if the brief is missing or never started a session.

    Each role brief is paid lazily, on that role's first gated action, so a Claude session that
    never ends a turn never pays for a stop supervisor."""
    retire_legacy_sessions(claude_session_id)
    sid = read_session_id(cwd, claude_session_id, role)
    if sid:
        return sid, ""
    root = canonical_project_key(cwd)
    # Always the COMPOSED brief: the harness's standing instructions plus the project's own
    # (or DEFAULT_BRIEF when it has none, so a missing brief never fail-closes every commit).
    # Piping the project file's raw bytes, as this did, silently dropped the system prompt.
    composed = compose_brief(root)
    new_sid, code, out, err = brief_and_capture_text(
        root, composed, timeout=timeout, on_cancel=on_cancel
    )
    if not new_sid:
        detail = (err or "").strip() or "no session id captured (exit {})".format(code)
        return None, "brief did not start a reviewer session: {}".format(detail)
    # Keyed to the session that RECEIVED it, not to the one that wins the compare-and-set below:
    # if another hook briefed concurrently, its own call recorded its own brief, and every later
    # audit resolves the brief through whichever session id it actually resumes.
    record_session_brief(new_sid, composed)
    # Compare-and-set, not a plain write: another hook in this same Claude session may have
    # briefed while we were briefing, and both must end up on ONE reviewer.
    return write_session_id_if_absent(cwd, claude_session_id, new_sid, role), ""


# --------------------------------------------------------------------------
# claude backend: a Claude Code child, for a Claude Code user who wants an independent
# reviewer without installing anything else.
#
# It runs the `claude` CLI the operator already has, and lets that CLI authenticate itself.
# No credential is read, copied or forwarded here -- the distinction between "drive the tool
# you installed" and "take its token elsewhere" is the whole reason this is a reasonable thing
# for the plugin to do at all.
#
# Every flag below was chosen from a measured run, not from documentation:
#
#   --safe-mode          MANDATORY. Without it the child loads the operator's own hooks: in one
#                        measured run a child fired them 19 times -- SessionStart briefed a
#                        reviewer, then the Stop hook looped the child to its nudge cap and the
#                        review came back empty. Safe mode disables hooks, plugins, MCP servers
#                        and CLAUDE.md, and unlike `--bare` it keeps subscription auth working.
#   --permission-mode    `dontAsk`, NOT `auto`. Under `auto` an allowlist is not exclusive:
#                        anything unnamed goes to the classifier, which was measured approving a
#                        file write, and the model located writers at runtime with `ToolSearch`
#                        and tried to delegate through subagents. `dontAsk` denies everything
#                        not explicitly allowed -- including tools nobody enumerated and tools a
#                        later CLI version introduces -- and held across repeated attempts by a
#                        model actively trying to escape it.
#   --allowedTools       read and search only. There is deliberately no git access: Claude's
#                        `Read` sees the working TREE, not the index, and granting a shell to
#                        reach the index would reopen the `diff.external` execution hole the pi
#                        extension had to close. The staged patch is inlined into the prompt.
#   --disallowedTools    belt and braces over the fail-closed mode, so the dangerous names are
#                        refused by rule as well as by default.
#   --session-id         the harness MINTS the id rather than parsing one out of the output,
#                        which the CLI supports and the other two backends do not.
# --------------------------------------------------------------------------
CLAUDE_ALLOWED_TOOLS = ("Read", "Grep", "Glob")


CLAUDE_DENIED_TOOLS = ("Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch")


CLAUDE_PERMISSION_MODE = "dontAsk"


def new_claude_session_id():
    return str(uuid.uuid4())


def claude_argv(prompt, session_id, resume=False, model=None, effort=None,
                system_prompt=None):
    """The command line for one reviewer turn. `prompt` travels on STDIN, never in argv.

    A staged patch can be large, argv has a hard size limit, and anything on a command line is
    visible in the process table -- three reasons the prompt is piped instead. The BRIEF is the
    exception: it is bounded, it is not the change under review, and `--append-system-prompt`
    is the only channel left once safe mode has disabled CLAUDE.md, plugins and hooks.
    """
    argv = [
        claude_bin(), "-p",
        "--output-format", "json",
        "--safe-mode",
        "--permission-mode", CLAUDE_PERMISSION_MODE,
    ]
    argv += ["--resume", str(session_id)] if resume else ["--session-id", str(session_id)]
    if system_prompt:
        argv += ["--append-system-prompt", str(system_prompt)]
    model = model if model is not None else reviewer_model("claude")
    if model:
        argv += ["--model", str(model)]
    effort = effort if effort is not None else reviewer_effort("claude")
    if effort:
        argv += ["--effort", str(effort)]
    argv += ["--allowedTools"] + list(CLAUDE_ALLOWED_TOOLS)
    argv += ["--disallowedTools"] + list(CLAUDE_DENIED_TOOLS)
    return argv


def _claude_events(stdout):
    """The CLI's JSON output as a list of event dicts, whichever shape it used.

    `--output-format json` returns ONE object normally and an ARRAY of events when the CLI is
    verbose. Both shapes were observed on the same machine, so both are read rather than one
    being assumed.
    """
    try:
        payload = json.loads(stdout or "")
    except ValueError:
        return []
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _claude_result_entry(stdout):
    finals = [item for item in _claude_events(stdout) if item.get("type") == "result"]
    return finals[-1] if finals else None


def parse_claude_result(stdout):
    """The reviewer's final text, or "" when the run produced no result entry.

    Empty rather than a guess: an empty verdict is already handled by the gate as a
    fail-closed outcome, whereas inventing text would be inventing a ruling.
    """
    entry = _claude_result_entry(stdout)
    return str((entry or {}).get("result") or "").strip()


def _claude_limit_events(stdout):
    """`rate_limit_event` entries that report a BLOCKING state, which is not most of them.

    MEASURED: the CLI emits one of these on every ordinary run, carrying
    `rate_limit_info.status: "allowed"` plus a reset time -- it is a quota REPORT, not an error.
    Treating its presence as exhaustion is what turned a live APPROVED verdict into "usage limit
    reached, commit blocked" in the release matrix. Anything whose status starts with `allowed`
    is therefore fine; only the rest are candidates. (`overageStatus` is a separate field about
    overage billing and is deliberately not consulted: it reads `rejected` on an account that is
    simply not using overage.)
    """
    blocking = []
    for item in _claude_events(stdout):
        if item.get("type") != "rate_limit_event":
            continue
        status = str((item.get("rate_limit_info") or {}).get("status") or "").strip().lower()
        if not status.startswith("allowed"):
            blocking.append(item)
    return blocking


def claude_rate_limit_detail(stdout):
    """The provider's own sentence when a usage limit stopped the review, else None.

    Worth its own detector because the gate is fail-CLOSED: an exhausted subscription would
    otherwise present as a permanently blocked repository with a generic error, and the remedy
    (wait, or switch backend) would be named nowhere.

    Both branches require the turn to have FAILED. A successful review is never reclassified as
    a usage limit, whatever its text says -- a reviewer quoting the words "rate limit" out of the
    diff it is auditing must not be able to convert its own approval into a block.
    """
    entry = _claude_result_entry(stdout)
    text = str((entry or {}).get("result") or "").strip()
    # No result entry at all counts as failed: the turn produced no verdict either way.
    errored = entry is None or bool(entry.get("is_error"))
    if not errored:
        return None
    if _claude_limit_events(stdout):
        return text or "the Claude usage limit was reached"
    if re.search(r"usage limit|rate limit", text, re.I):
        return text
    return None


def run_claude_review(cwd, claude_session_id, prompt, timeout=DEFAULT_TIMEOUT,
                      effort=None, role=ROLE_REVIEWER, on_cancel=None):
    """One Claude Code reviewer turn against `cwd`, resuming this project's own child session.

    The first turn mints a session id and publishes it compare-and-set; later turns resume it,
    so the reviewer keeps what it read and can give its previous findings a disposition.
    """
    stored = read_claude_session(cwd, claude_session_id, role)
    session_id = stored or new_claude_session_id()
    # The brief goes out on the FIRST turn only, exactly as with pi: a resumed child already
    # holds it, and re-appending it would grow the system prompt on every commit for no gain.
    argv = claude_argv(
        prompt, session_id, resume=bool(stored), effort=effort,
        system_prompt=None if stored else compose_brief(cwd),
    )
    code, out, err = _run(
        argv, input_bytes=(prompt or "").encode("utf-8"), timeout=timeout, cwd=str(cwd),
        on_cancel=on_cancel,
    )
    if not stored:
        # Only after a turn that actually ran: publishing an id the CLI never accepted would
        # make the next attempt resume a session that does not exist.
        if code == 0 or _claude_result_entry(out) is not None:
            session_id = write_claude_session_if_absent(
                cwd, claude_session_id, session_id, role
            )
    limit = claude_rate_limit_detail(out)
    if limit:
        return code or 1, "", (
            "the Claude usage limit was reached, so the review could not run: {}".format(limit)
        )
    final = parse_claude_result(out)
    entry = _claude_result_entry(out) or {}
    if entry.get("is_error") and not final.strip():
        return code or 1, "", (err or "the reviewer returned an error with no text").strip()
    return code, final, err
