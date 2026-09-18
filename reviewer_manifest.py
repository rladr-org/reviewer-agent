#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Manifest layer of the reviewer package: which files THIS agent touched, and in which repos.

Step 5 of LIB_SPLIT_PLAN.md. Imports `state` and `transcript` and nothing else;
`tools/check_split_layering.py` fails if it ever reaches forward in the layer order.

Why it exists at all, from two failures observed live on 2026-07-31, both one root cause. The
stop supervisor is asked for "files the AGENT created or modified this session" and told to
excuse pre-existing dirty files it did not touch -- but nothing ever told it which those were,
so it inferred from `git status` in a single checkout. A session whose work happened in another
directory looked idle, and in a SHARED checkout the supervisor credited the agent with a
concurrent session's in-flight files. Attribution therefore comes from the TRANSCRIPT, which
records what this agent did, and the repository set from the touched FILES rather than from cwd.

Deliberately UNDER-inclusive: only tools carrying a structured path argument are counted, so a
`cp` or a heredoc inside `Bash` is invisible here. That asymmetry is the safe one -- an
under-reported manifest makes the supervisor ask, while an over-reported one makes it direct
commits of files the agent never wrote, which is the second failure all over again.

`_TOPLEVEL_CACHE` is shared MUTABLE state and is read DIRECTLY off the facade by
tests/test_reviewer_lib.py:939 and :954. It must be MUTATED and never rebound
(LIB_SPLIT_PLAN.md 5.7, pinned by tests/test_split_shared_state.py).

Everything here is re-exported by `reviewer_lib` for back-compat, so existing
`rl.session_attribution(...)` call sites are unchanged.
"""
import datetime
import json
import os
import re
import subprocess
from pathlib import Path

from reviewer_state import (
    _tunable_int,
)
from reviewer_transcript import (
    _TASK_NOTIFICATION_TUID,
    _event_content,
    _event_epoch_seconds,
    _is_human_prompt,
    _transcript_events,
)

# --------------------------------------------------------------------------
# Session file manifest: which files THIS agent touched, and in which repos.
#
# Observed live on 2026-07-31, both from one root cause. STOP_SUPERVISE_PROMPT
# check (a) asks the supervisor for "files the AGENT created or modified this
# session", and explicitly excuses "pre-existing dirty files the agent did NOT
# touch" -- but nothing ever told it which those were. It received the operator's
# messages and the agent's last reply, and had to infer the rest from `git
# status` in a single checkout, `data["cwd"]`. Two failures followed:
#
#   1. a session whose work happened in ANOTHER working directory looked idle.
#      cwd was clean of it, so the supervisor reached for that repo's unrelated
#      outstanding work and directed the agent to do it instead;
#   2. in a SHARED checkout it credited the agent with a concurrent session's
#      in-flight files, directing a commit of a document another live session
#      was editing at that moment.
#
# Attribution therefore comes from the transcript, which records what THIS agent
# did, and the repository set comes from the touched FILES rather than from cwd.
# Both defects close with one mechanism.
#
# Deliberately UNDER-inclusive. Only tools carrying a structured path argument
# are counted; `Bash` carries an opaque command string, so a `cp` or a heredoc is
# invisible here. That asymmetry is the safe one: an under-reported manifest
# makes the supervisor ask the agent, while an over-reported one makes it direct
# commits of files the agent never wrote -- which is defect 2 all over again.
# --------------------------------------------------------------------------
#: Tools whose invocation MODIFIES the named path. `Read` is deliberately absent:
#: looking at a file is not touching it.
MODIFYING_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")


#: Input keys naming the modified file, across those tools.
_PATH_KEYS = ("file_path", "notebook_path")


#: Per-repository listing cap, and a total budget for FILE lines across all roots.
#: Files may be elided; ROOTS never are (see render_manifest -- the supervisor is
#: told to check the repositories named here, so a root dropped to fit a budget
#: silently removes a whole checkout from the hygiene check). The total budget is
#: what keeps a many-root session from flooding the prompt now that roots are
#: structural: it shrinks the per-root cap instead of truncating the block.
_MANIFEST_MAX_PER_ROOT = 40


_MANIFEST_MAX_FILE_LINES = 600


def _tool_outcomes(events):
    """{tool_use_id: True|False} -- did that call SUCCEED? A call with no recorded
    result (an interrupted turn) is absent, and callers treat absent as unknown."""
    outcomes = {}
    for ev in events:
        content = _event_content(ev)
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            uid = part.get("tool_use_id")
            if uid:
                outcomes[uid] = not bool(part.get("is_error"))
    return outcomes


def session_attribution(transcript_path):
    """What this session demonstrably touched, and how far that record can be trusted.

    Returns `confirmed` ({repo_root: [relative paths]}), `uncertain` (paths whose
    modifying call FAILED or has no recorded result), `bash_calls` (EVERY Bash
    invocation, whatever its outcome, since none of them carries a parseable file
    effect), and `complete` -- true only when every modifying call succeeded and no
    Bash ran at all.

    `complete` is the load-bearing field. The supervisor may treat a file's ABSENCE
    from the manifest as proof it is not this agent's work ONLY when attribution is
    complete; otherwise absence proves nothing and it must ask the agent. Three ways
    the first version got this wrong, all reviewer-found:

      - a Bash write (cp, redirection, a heredoc) carries no structured path, so a
        session that wrote one file with Write and another with Bash produced a
        manifest that LOOKED complete while the Bash-written file read as somebody
        else's;
      - only the tool_use was read, never its result, so a REJECTED edit counted as
        a touch. Observed live: a Write refused with "File has not been read yet"
        would have been attributed, and had a concurrent session then dirtied that
        path, the supervisor would have directed a commit of the other session's file;
      - a path is confirmed if ANY of its calls succeeded, so the ordinary
        fail-then-retry sequence attributes correctly instead of poisoning the path.
    """
    events = _transcript_events(transcript_path)
    outcomes = _tool_outcomes(events)
    succeeded, failed, bash_calls = set(), set(), 0
    for ev in events:
        content = _event_content(ev)
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "tool_use":
                continue
            name = part.get("name")
            inp = part.get("input")
            ok = outcomes.get(part.get("id"))
            if name == "Bash":
                # EVERY Bash invocation counts, whatever its outcome. Counting only
                # successful ones was a fail-open: exit status is not a statement
                # about the filesystem. `cp a b && false` writes and then fails; a
                # pipeline can write before it breaks; an interrupted call records no
                # result at all yet may have run to completion. The only safe reading
                # is that any Bash invocation MAY have touched files invisibly.
                bash_calls += 1
                continue
            if name not in MODIFYING_TOOLS or not isinstance(inp, dict):
                continue
            for key in _PATH_KEYS:
                val = inp.get(key)
                if isinstance(val, str) and val.strip():
                    (succeeded if ok else failed).add(val.strip())
    # A path that ever succeeded is the agent's, whatever else was attempted on it.
    uncertain = sorted(failed - succeeded)
    grouped = {}
    for path in sorted(succeeded):
        root = git_toplevel(path)
        if root:
            try:
                rel = str(Path(path).resolve().relative_to(Path(root).resolve()))
            except ValueError:
                rel = path
        else:
            root, rel = "", path
        grouped.setdefault(root, []).append(rel)
    return {
        "confirmed": {root: sorted(grouped[root]) for root in sorted(grouped)},
        "uncertain": uncertain,
        "bash_calls": bash_calls,
        "complete": not uncertain and bash_calls == 0,
    }


def session_touched_files(transcript_path):
    """Absolute paths this session is CONFIRMED to have created or modified, sorted.

    Confirmed only: see `session_attribution` for failed calls and the Bash blind
    spot. Empty for an unreadable transcript."""
    att = session_attribution(transcript_path)
    out = []
    for root, paths in att["confirmed"].items():
        out.extend(str(Path(root) / p) if root else p for p in paths)
    return sorted(out)


#: Directory -> repo root, for the lifetime of this process. The stop hook runs at
#: EVERY turn end and a session can touch hundreds of files, nearly all of them
#: sharing a handful of directories; without this each file would cost its own
#: subprocess and make the turn boundary visibly slower.
_TOPLEVEL_CACHE = {}


def git_toplevel(path):
    """The git repository root containing `path`, or None. Resolved with git, not
    by walking for a `.git` directory, so a linked worktree answers correctly."""
    start = Path(path)
    directory = start if start.is_dir() else start.parent
    key = str(directory)
    if key in _TOPLEVEL_CACHE:
        return _TOPLEVEL_CACHE[key]
    try:
        p = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        _TOPLEVEL_CACHE[key] = None   # a failure is cached too: it will fail again
        return None
    out = (p.stdout or "").strip()
    root = out if p.returncode == 0 and out else None
    _TOPLEVEL_CACHE[key] = root
    return root


def session_touched_roots(transcript_path):
    """{repo_root: [paths relative to it]} for CONFIRMED touches, ordered by root.

    Files under no repository are grouped under the empty-string key with their
    absolute paths, so they are still reported and are never silently attributed
    to cwd's repository."""
    return session_attribution(transcript_path)["confirmed"]


def render_manifest(attribution):
    """The manifest as a prompt block, or "" when there is nothing at all to say.

    Empty means EMPTY: a heading with nothing under it reads as the positive claim
    "this agent touched no files", which is an inference to draw from evidence, not
    from a formatting artifact.

    Every repository root is ALWAYS rendered. Only per-repository FILE lines may be
    elided, and eliding any marks attribution incomplete -- because the supervisor is
    told to check the repositories named here, so a root dropped to fit a character
    budget would silently remove an entire checkout from the hygiene check."""
    roots = attribution.get("confirmed") or {}
    uncertain = attribution.get("uncertain") or []
    bash_calls = attribution.get("bash_calls") or 0
    if not roots and not uncertain and not bash_calls:
        return ""
    # Shrink the per-root cap rather than the root SET when there are many roots.
    per_root = min(_MANIFEST_MAX_PER_ROOT,
                   max(1, _MANIFEST_MAX_FILE_LINES // max(1, len(roots))))
    elided = any(len(p) > per_root for p in roots.values())
    complete = bool(attribution.get("complete")) and not elided
    total = sum(len(v) for v in roots.values())

    lines = [
        "=== SESSION FILE MANIFEST ===",
        "ATTRIBUTION: {}".format("COMPLETE" if complete else "INCOMPLETE"),
        "Files this agent is CONFIRMED to have created or modified this session ({} "
        "total), grouped by the repository each belongs to. Derived from the session "
        "transcript, so for the files it DOES list this is authoritative and `git "
        "status` is not.".format(total),
    ]
    if complete:
        lines.append(
            "Attribution is COMPLETE: every modifying call succeeded, no Bash ran, and "
            "nothing was elided. A dirty path absent from this list was NOT touched by "
            "this agent.")
    else:
        reasons = []
        if uncertain:
            reasons.append(
                "{} modifying call(s) FAILED or have no recorded result".format(
                    len(uncertain)))
        if bash_calls:
            reasons.append(
                "{} Bash command(s) were invoked, and a Bash write (cp, redirection, "
                "heredoc) carries no structured path -- a failed or interrupted one may "
                "still have written before it stopped".format(bash_calls))
        if elided:
            reasons.append("some per-repository file lists were elided for length")
        lines.append(
            "Attribution is INCOMPLETE ({}). Absence from this list is therefore NOT "
            "evidence that a file belongs to someone else. Do not attribute an unlisted "
            "file to this agent either -- ASK the agent what it touched.".format(
                "; ".join(reasons)))
    lines.append("")

    for root, paths in roots.items():
        lines.append("{} [{} file(s)]".format(root or "(not in any git repository)",
                                              len(paths)))
        for rel in paths[:per_root]:
            lines.append("  " + rel)
        if len(paths) > per_root:
            lines.append("  ... and {} more in this repository (list elided, NOT "
                         "unattributed)".format(len(paths) - per_root))
    if uncertain:
        lines.append("UNVERIFIED (call failed or no result recorded) -- may or may not "
                     "have been modified:")
        for path in uncertain[:_MANIFEST_MAX_PER_ROOT]:
            lines.append("  " + path)
        if len(uncertain) > _MANIFEST_MAX_PER_ROOT:
            lines.append("  ... and {} more".format(
                len(uncertain) - _MANIFEST_MAX_PER_ROOT))
    lines.append("")
    return "\n".join(lines) + "\n"


def session_manifest_block(transcript_path):
    """The manifest for a transcript. Thin wrapper so rendering stays pure/testable."""
    return render_manifest(session_attribution(transcript_path))


def _is_task_notification(ev):
    """A background task's completion notification (a user event whose string content
    starts with <task-notification>). It re-invoked the session -> a turn boundary."""
    if ev.get("type") != "user":
        return False
    c = _event_content(ev)
    return isinstance(c, str) and c.lstrip().startswith("<task-notification>")


def _is_monitor_event(ev):
    """A Monitor progress notification: a <task-notification> carrying an <event> (the
    Monitor tool relays one per event, e.g. per training epoch, and instructs the agent to
    "keep working -- do not poll"), as opposed to a terminal <status>completed</status>.
    Its presence AS the current turn's boundary means the agent was re-invoked by an active
    monitor and is parked waiting for the next event."""
    if not _is_task_notification(ev):
        return False
    c = _event_content(ev)
    return isinstance(c, str) and "<event>" in c


# --------------------------------------------------------------------------
# stopContinue: detect that the agent ENDED ITS TURN while a background task it
# launched is still running (a run_in_background sub-agent or shell that the
# harness auto-re-invokes on completion). In that case stopContinue must NOT nudge
# -- the agent is correctly parked, and forcing it to continue only makes it spin.
# Scoped to the CURRENT turn (reset at the last human prompt OR the last
# <task-notification>) so a stale fire-and-forget launch from an earlier turn can
# never mute stopContinue for a whole autonomous session. See
# STOPCONTINUE_BG_WAIT_PLAN.md for the verified transcript schema.
#
# The transcript schema constants, the per-process parse cache and the event accessors this
# section is built on live in reviewer_transcript (LIB_SPLIT_PLAN.md step 2), because the
# manifest and the prompt/operator relay parse the same transcript. This header moved here
# with its code at split step 8, having been left behind in the facade by step 5.
# --------------------------------------------------------------------------
# Tools whose launches are BACKGROUND BY DEFAULT. The Agent tool's contract is now
# "Subagents run in the background by default ... Pass run_in_background: false for a
# synchronous run", so for these an ABSENT flag means background. Every other tool (Bash)
# keeps foreground as its default and must opt in explicitly (Amendment 7).
_BACKGROUND_BY_DEFAULT_TOOLS = ("Agent", "Task")


def _launch_is_background(block):
    """Does this tool_use block start work that OUTLIVES the turn? Reads the explicit
    run_in_background flag when present, and otherwise falls back to the TOOL'S OWN default.
    Requiring a truthy flag was correct when every background launch had to ask for it; once
    the subagent tools flipped their default, that test silently reclassified every
    default-background launch as foreground and stopContinue nudged straight through it."""
    rib = (block.get("input") or {}).get("run_in_background")
    if rib is not None:
        return bool(rib)
    return block.get("name") in _BACKGROUND_BY_DEFAULT_TOOLS


def _background_launch_ids(ev):
    """tool_use ids of background launches in an assistant event. Foreground/blocking
    sub-agents return inline and are excluded -- they never end the turn to wait."""
    ids = []
    c = _event_content(ev)
    if ev.get("type") == "assistant" and isinstance(c, list):
        for b in c:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and _launch_is_background(b)):
                if b.get("id"):
                    ids.append(b["id"])
    return ids


def _completion_tool_use_id(ev):
    """The launching <tool-use-id> a task-notification resolves, or None."""
    if not _is_task_notification(ev):
        return None
    c = _event_content(ev)
    m = _TASK_NOTIFICATION_TUID.search(c) if isinstance(c, str) else None
    return m.group(1) if m else None


# Active-Monitor lifecycle (plan Amendment 2): the Monitor tool is NOT a
# run_in_background launch, and between its <event> notifications the turn boundary can
# be anything (a human prompt, another completion) -- so the LAUNCH itself must count
# as an outstanding wait until the monitor terminates.
_MONITOR_STARTED = re.compile(r"Monitor started \(task (\S+?), (persistent|timeout (\d+)ms)")


_TASK_STOPPED_MSG = re.compile(r"Successfully stopped task:\s*(\S+)")


_TASK_NOTIFICATION_TASK_ID = re.compile(r"<task-id>\s*([^<\s]+)\s*</task-id>")


# Background SHELL lifecycle (Amendment 6). A `run_in_background` Bash launch is
# acknowledged with this line, which is the only place the harness TASK id appears (the
# launch scan sees only tool_use ids, and only within the current turn). Tracking the task
# id across the WHOLE transcript is what keeps a multi-hour shell suppressing stopContinue
# through the later turns that probe and report on it.
#
# The capture is the strict harness id grammar `[A-Za-z0-9_-]+` (mirroring _ASYNC_AGENT_STARTED),
# NOT a loose `[^\s.,]+`. A loose group matches the pattern's OWN source text whenever that text
# appears in the transcript -- reading this file while debugging the watchdog, a pasted bug report
# -- minting the phantom id `\s*([^\s`, which never receives a completion notification and so
# suppresses stopContinue forever: the stop path takes the background-wait early return every tick,
# the idle countdown never starts, and the recovery cron never tears down. See
# docs/plans/watchdog_bgwait_phantom_task.md.
_BG_SHELL_STARTED = re.compile(r"Command running in background with ID:\s*([A-Za-z0-9_-]+)")


def _background_shell_task_ids(events):
    """Harness task ids of every background shell launched anywhere in the transcript.

    Defense in depth behind the strict `_BG_SHELL_STARTED` charset: any capture that is not a
    harness id (`[A-Za-z0-9_-]+`) is dropped, so quoted pattern text can never mint a phantom id
    even if the pattern is later loosened."""
    ids = set()
    for ev in events:
        for text in _tool_result_texts(ev):
            for m in _BG_SHELL_STARTED.finditer(text):
                cap = m.group(1)
                if re.fullmatch(r"[A-Za-z0-9_-]+", cap):
                    ids.add(cap)
    return ids


def _event_text_blob(ev):
    """All human-readable text carried by an event, so a task id can be spotted wherever it
    is mentioned (assistant prose, tool_result bodies, plain string content)."""
    parts = []
    c = _event_content(ev)
    if isinstance(c, str):
        parts.append(c)
    elif isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            for key in ("text", "content"):
                v = b.get(key)
                if isinstance(v, str):
                    parts.append(v)
                elif isinstance(v, list):
                    parts.extend(x.get("text", "") for x in v if isinstance(x, dict))
    return " ".join(parts)


def _background_shell_last_signal(events):
    """Map each background-shell task id to the epoch seconds of the LAST event mentioning
    it. That last mention is the liveness signal: an agent genuinely parked on a shell keeps
    naming it while it reports progress, whereas a superseded or silently dead one goes
    quiet. Bounding on it stops a watcher that never emitted a terminal notification from
    muting stopContinue forever. Ids with no dateable mention are omitted (fail-open:
    prefer a wasted nudge over indefinite muting)."""
    ids = _background_shell_task_ids(events)
    if not ids:
        return {}
    last = {}
    for ev in events:
        ts = _event_epoch_seconds(ev)
        if ts is None:
            continue
        blob = _event_text_blob(ev)
        if not blob:
            continue
        for tid in ids:
            if tid in blob and ts > last.get(tid, 0):
                last[tid] = ts
    return last


# Async SUBAGENT lifecycle (Amendment 7). The launch ack is the only place the harness
# AGENT id appears -- the launch scan sees tool_use ids, and only inside the current turn.
# Tracking the agent id across the WHOLE transcript is what keeps a FAN-OUT suppressing:
# when the first of N subagents completes, its <task-notification> becomes the new turn
# boundary and the still-running siblings drop out of the current-turn scan entirely.
_ASYNC_AGENT_STARTED = re.compile(
    r"Async agent launched successfully.*?\bagentId:\s*([0-9A-Za-z_-]+)", re.S)

# A SendMessage RESUME of an async subagent (Amendment 8). Resuming a completed subagent runs
# it again, but the harness emits this ack -- NOT a fresh "Async agent launched" line -- and
# the agent's earlier completion <task-notification> stays in the transcript. Without a
# resume-aware scan, `_live_async_agent_ids` reads a resumed-and-running subagent as long dead
# and lets stopContinue fire on top of live work.
_ASYNC_AGENT_RESUMED = re.compile(r'"resumedAgentId"\s*:\s*"([0-9A-Za-z_-]+)"')


def _async_agent_launch_times(events):
    """Map each async subagent's agent id to the epoch seconds of its launch ack. Ids whose
    ack carries no parseable timestamp are OMITTED -- the unified fail-open no-timestamp rule
    (Amendment 3): an undateable launch cannot be bounded, so it must never suppress."""
    launched = {}
    for ev in events:
        ts = _event_epoch_seconds(ev)
        if ts is None:
            continue
        for text in _tool_result_texts(ev):
            for m in _ASYNC_AGENT_STARTED.finditer(text):
                aid = m.group(1)
                # Keep the EARLIEST ack for an id: the launch is what the window is measured
                # from, and re-reading the same transcript must not slide the deadline.
                if aid not in launched or ts < launched[aid]:
                    launched[aid] = ts
    return launched


def _agent_lifecycle_signals(ev):
    """(start_ids, end_ids) an event carries for async subagents.

    A START is a launch ack or a resume ack; an END is a terminal <status> task-notification,
    a TaskStop, or a "successfully stopped" result. Classification is by CONTENT only -- the
    caller uses transcript ORDER, not timestamps, to decide which is latest, so an UNDATED
    terminal that arrives after a resume still ends the subagent (Amendment 8). Timestamps
    only bound staleness, never ordering."""
    starts, ends = set(), set()
    for text in _tool_result_texts(ev):
        for m in _ASYNC_AGENT_STARTED.finditer(text):
            starts.add(m.group(1))
        for m in _ASYNC_AGENT_RESUMED.finditer(text):
            starts.add(m.group(1))
        m = _TASK_STOPPED_MSG.search(text)
        if m:
            ends.add(m.group(1))
    c = _event_content(ev)
    if ev.get("type") == "assistant" and isinstance(c, list):
        for b in c:
            if (isinstance(b, dict) and b.get("type") == "tool_use"
                    and b.get("name") == "TaskStop"):
                tid = (b.get("input") or {}).get("task_id")
                if tid:
                    ends.add(tid)
    for text in _notification_texts(ev):
        if "<status>" in text:
            m = _TASK_NOTIFICATION_TASK_ID.search(text)
            if m:
                ends.add(m.group(1))
    return starts, ends


_ASYNC_AGENT_STALE_SECS_DEFAULT = 4 * 3600


def _async_agent_stale_secs():
    """Staleness window for async SUBAGENTS, measured from the LAUNCH.

    Not from the last mention (the background-shell rule): the launch ack instructs the agent
    never to quote the agentId, so a perfectly healthy subagent's last mention is always its
    own ack -- last-mention would be launch-time wearing a misleading justification.

    Default 4h: far longer than any observed subagent (5.5-18 min in the evidenced session)
    yet still bounded, so a subagent that dies without ever emitting a terminal notification
    cannot mute stopContinue for the rest of the session. Shorter than the 12h shell window
    on purpose -- a multi-hour GPU shell is silent BY DESIGN, whereas a subagent silent for
    four hours is more likely dead than working, and the nudge is what recovers it (the
    session is otherwise idle awaiting a notification that will never arrive)."""
    return _tunable_int("REVIEWER_ASYNC_AGENT_STALE_SECS", "asyncAgentStaleSecs",
                        _ASYNC_AGENT_STALE_SECS_DEFAULT, 60)


# Both background launch ACKS. They are inline tool_results for the launching tool_use id,
# but they announce work that is still running -- so they must never read as "the result came
# back inline". Getting this exclusion wrong would silently undo Amendment 7 for every
# fan-out, which is why it has a dedicated regression test.
_LAUNCH_ACK_PATTERNS = (_ASYNC_AGENT_STARTED, _BG_SHELL_STARTED)


def _inline_resolved_tool_use_ids(ev):
    """tool_use ids whose result came back INLINE in this event -- direct evidence the work
    did not outlive the turn, whatever the launch flags said.

    Needed because `run_in_background` stopped being a reliable classifier (Amendment 7): the
    subagent tools default to background, so the launch scan now assumes background and this
    is what still recognizes a sub-agent that really did return inline. Evidence beats
    defaults in both directions."""
    ids = set()
    c = _event_content(ev)
    if ev.get("type") != "user" or not isinstance(c, list):
        return ids
    for b in c:
        if not (isinstance(b, dict) and b.get("type") == "tool_result"):
            continue
        tuid = b.get("tool_use_id")
        if not tuid:
            continue
        cc = b.get("content")
        if isinstance(cc, list):
            cc = " ".join(x.get("text", "") for x in cc if isinstance(x, dict))
        text = cc if isinstance(cc, str) else ""
        if any(p.search(text) for p in _LAUNCH_ACK_PATTERNS):
            continue
        ids.add(tuid)
    return ids


def _live_async_agent_ids(events, now=None):
    """Agent ids of async subagents still in flight: their LATEST lifecycle signal in
    transcript ORDER is a start, that start ack was dated, and it is inside the staleness
    window measured from it.

    A start is a launch ack OR a resume ack (Amendment 8): a SendMessage resume runs a
    completed subagent again. ORDER, not timestamps, decides which of a resume and a terminal
    signal is latest -- so an undated terminal <status> that arrives AFTER a resume still ends
    the agent, and a resume after a completion revives it. An undateable latest start cannot be
    bounded, so per the unified no-timestamp rule (Amendment 3) it never suppresses.

    `now` is injectable so a historical replay can ask "was it live AT THE STOP" rather than
    at wall clock, the convention Amendment 3 established for monitors."""
    last_kind = {}      # aid -> 'start'|'end' for its LATEST signal in transcript order
    last_start_ts = {}  # aid -> epoch of that latest start (None if the start ack was undated)
    for ev in events:
        ts = _event_epoch_seconds(ev)
        starts, ends = _agent_lifecycle_signals(ev)
        for aid in starts:
            last_kind[aid] = "start"
            last_start_ts[aid] = ts
        for aid in ends:
            last_kind[aid] = "end"
    if not last_kind:
        return set()
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    stale = _async_agent_stale_secs()
    live = set()
    for aid, kind in last_kind.items():
        if kind != "start":
            continue
        ts = last_start_ts.get(aid)
        if ts is not None and 0 <= now - ts <= stale:
            live.add(aid)
    return live


def _tool_result_texts(ev):
    """String contents of tool_result blocks in a user event (list-content carrier)."""
    out = []
    c = _event_content(ev)
    if ev.get("type") == "user" and isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                cc = b.get("content")
                if isinstance(cc, str):
                    out.append(cc)
                elif isinstance(cc, list):
                    out.append(" ".join(
                        x.get("text", "") for x in cc if isinstance(x, dict)))
    return out


_MONITOR_STALE_SECS_DEFAULT = 3600


def _monitor_stale_secs():
    """Staleness window: a monitor with no signal (launch or <event>) inside this window
    no longer suppresses. Bounds the muting window against ANY missed termination signal
    (Amendment 3). Env-overridable, clamped to >= 60s."""
    return _tunable_int("REVIEWER_MONITOR_STALE_SECS", "monitorStaleSecs",
                        _MONITOR_STALE_SECS_DEFAULT, 60)


_BG_SHELL_STALE_SECS_DEFAULT = 12 * 3600


def _bg_shell_stale_secs():
    """Staleness window for background SHELLS -- deliberately far longer than the monitor
    window. A monitor emits periodic events, so an hour of silence means something is wrong.
    A background shell (an 8-hour GPU run, a long build) is SILENT BY DESIGN, and nudging the
    agent mid-wait makes it spend tokens for nothing. Long, but still bounded, so a watcher
    that dies without ever emitting a terminal notification cannot mute stopContinue for the
    rest of the session. Env-overridable via REVIEWER_BG_SHELL_STALE_SECS (clamp >= 60s);
    set it very high for multi-day jobs."""
    return _tunable_int("REVIEWER_BG_SHELL_STALE_SECS", "bgShellStaleSecs",
                        _BG_SHELL_STALE_SECS_DEFAULT, 60)


def _notification_texts(ev):
    """<task-notification> payloads from EVERY delivery path the harness uses: a user
    event (string content), a queue-operation event (string `content`), or an attachment
    event (`attachment.prompt`). Simultaneous notifications are queued/attached instead
    of written as user events -- the evidenced Amendment-3 leak -- so the monitor
    lifecycle must read all three."""
    out = []
    t = ev.get("type")
    if t == "user":
        c = _event_content(ev)
        if isinstance(c, str) and "<task-notification>" in c:
            out.append(c)
    elif t == "queue-operation":
        c = ev.get("content")
        if isinstance(c, str) and "<task-notification>" in c:
            out.append(c)
    elif t == "attachment":
        att = ev.get("attachment")
        p = att.get("prompt") if isinstance(att, dict) else None
        if isinstance(p, str) and "<task-notification>" in p:
            out.append(p)
    return out


def _active_monitor_ids(events, now=None):
    """Task ids of Monitor launches still ACTIVE: seen "Monitor started (task <id>...)"
    with (a) no later TaskStop of that id, (b) no terminal <status> notification for that
    <task-id> on ANY delivery path (user / queue-operation / attachment), (c) the timeout
    window not yet expired (timeout monitors), and (d) a SIGNAL -- the launch or an
    <event> for that id -- inside the staleness window, so a monitor that goes quiet can
    only mute stopContinue for a bounded time. A launch with no parseable timestamp is
    never active (fail-open: prefer a wasted nudge over muting stopContinue
    indefinitely)."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    active = {}       # task_id -> deadline epoch seconds (None = no deadline)
    last_signal = {}  # task_id -> epoch seconds of the launch / latest <event>
    for ev in events:
        # Activation: the Monitor tool's start acknowledgement. Requires a parseable
        # launch timestamp (unified no-timestamp rule) so the muting window is bounded.
        for text in _tool_result_texts(ev):
            m = _MONITOR_STARTED.search(text)
            if not m:
                continue
            tid = m.group(1)
            persistent = m.group(2) == "persistent"
            timeout_ms = int(m.group(3)) if m.group(3) else 0
            # Structured metadata refines the text parse, but only for keys actually
            # present -- a partial toolUseResult must not clobber a text-confirmed mode.
            tur = ev.get("toolUseResult")
            if isinstance(tur, dict) and tur.get("taskId"):
                tid = tur["taskId"]
                if "persistent" in tur:
                    persistent = bool(tur.get("persistent"))
                if not persistent and "timeoutMs" in tur:
                    timeout_ms = int(tur.get("timeoutMs") or 0)
            launched_at = _event_epoch_seconds(ev)
            if launched_at is None:
                continue
            if persistent:
                active[tid] = None
                last_signal[tid] = launched_at
            elif timeout_ms > 0:
                active[tid] = launched_at + timeout_ms / 1000.0
                last_signal[tid] = launched_at
        # Deactivation: an explicit TaskStop of that task (keyed on the tool_use input,
        # fail-open -- even an attempted stop ends the suppression window).
        c = _event_content(ev)
        if ev.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == "TaskStop"):
                    active.pop((b.get("input") or {}).get("task_id"), None)
        for text in _tool_result_texts(ev):
            m = _TASK_STOPPED_MSG.search(text)
            if m:
                active.pop(m.group(1), None)
        # Notifications (any delivery path): a terminal <status> for the task ends the
        # window; an <event> is a fresh signal that refreshes the staleness clock (a
        # monitor <event> notification carries no <status>).
        for text in _notification_texts(ev):
            m = _TASK_NOTIFICATION_TASK_ID.search(text)
            if not m:
                continue
            tid = m.group(1)
            if "<status>" in text:
                active.pop(tid, None)
            elif "<event>" in text and tid in active:
                seen_at = _event_epoch_seconds(ev)
                if seen_at is not None:
                    last_signal[tid] = max(last_signal.get(tid, 0), seen_at)
    stale = _monitor_stale_secs()
    return [tid for tid, deadline in active.items()
            if (deadline is None or deadline > now)
            and now - last_signal.get(tid, 0) <= stale]


def _ended_monitor_ids(events):
    """Task ids explicitly ENDED: TaskStop'd (tool_use input or "Successfully stopped
    task" result) or a terminal <status> notification on any delivery path. Used to gate
    the monitor-event BOUNDARY rule (Amendment 3b): a boundary event for an ended monitor
    must not keep suppressing."""
    ended = set()
    for ev in events:
        c = _event_content(ev)
        if ev.get("type") == "assistant" and isinstance(c, list):
            for b in c:
                if (isinstance(b, dict) and b.get("type") == "tool_use"
                        and b.get("name") == "TaskStop"):
                    tid = (b.get("input") or {}).get("task_id")
                    if tid:
                        ended.add(tid)
        for text in _tool_result_texts(ev):
            m = _TASK_STOPPED_MSG.search(text)
            if m:
                ended.add(m.group(1))
        for text in _notification_texts(ev):
            if "<status>" in text:
                m = _TASK_NOTIFICATION_TASK_ID.search(text)
                if m:
                    ended.add(m.group(1))
    return ended


def _job_state_live_task_ids(transcript_path):
    """Background-task ids the HARNESS itself reports in flight for THIS session, from
    the background-job state file (<CLAUDE_JOB_DIR>/state.json `fan`). The harness
    removes a task's fan entry when it ends, so a live entry is ground-truth liveness --
    unlike transcript silence (a quiet-but-healthy monitor looks dead past the staleness
    window, Amendment 4) or the current-turn launch scan (a bg shell launched in an
    earlier turn is invisible to later turns, Amendment 5). Counts every task kind
    except "todo" -- todos are plan items sharing the fan array, distinguished by kind
    and by never carrying a real startedAt -- so a future harness kind suppresses
    correctly the first time it appears. Empty set when not a background job, the file
    is unreadable or malformed, or the state belongs to a DIFFERENT session (sessionId
    must equal the transcript filename stem) -- callers fall through to
    transcript-derived detection."""
    job_dir = os.environ.get("CLAUDE_JOB_DIR")
    if not job_dir:
        return set()
    try:
        state = json.loads((Path(job_dir) / "state.json").read_text())
    except (OSError, ValueError):
        return set()
    if not isinstance(state, dict):
        return set()
    sid = Path(transcript_path).stem if transcript_path else ""
    if not sid or state.get("sessionId") != sid:
        return set()
    fan = state.get("fan")
    if not isinstance(fan, list):
        return set()
    return {e["id"] for e in fan
            if isinstance(e, dict) and e.get("kind") != "todo"
            and e.get("startedAt") and e.get("id") and not e.get("doneAt")}


def has_outstanding_background_task(transcript_path):
    """True iff, in the CURRENT turn, the agent launched a background task
    (run_in_background sub-agent or shell) with no matching terminal
    <task-notification> yet -- i.e. the turn ended to PARK on background work the
    harness will auto-re-invoke the session for -- OR an armed Monitor is still active
    (its whole lifecycle suppresses: the harness re-invokes the session on every event,
    and the window is bounded by TaskStop / terminal status / timeout expiry).
    Fail-safe: any error -> False, so a detection bug falls through to normal
    supervision rather than wedging the agent."""
    try:
        events = _transcript_events(transcript_path)
        if not events:
            return False
        # Amendments 4+5: harness-state liveness. For background jobs the job state
        # file authoritatively lists in-flight tasks (monitors, shells, any future
        # kind), so parked-on-live-background-work keeps suppressing for the task's
        # whole lifecycle -- at any event cadence, across turn boundaries, and with no
        # visible launch ack (covering compaction/respawn windows). Transcript terminal
        # evidence (TaskStop / terminal <status>) still WINS over a lingering fan entry.
        live = _job_state_live_task_ids(transcript_path)
        if live and live - _ended_monitor_ids(events):
            return True
        if _active_monitor_ids(events):
            return True
        # Amendment 7: async SUBAGENTS, tracked across the whole transcript. This is what
        # covers a FAN-OUT -- the current-turn scan below loses every sibling the moment the
        # first completion notification becomes the turn boundary, so N-1 running subagents
        # went unseen and the agent was nudged to act on findings that did not exist yet.
        if _live_async_agent_ids(events):
            return True
        # Amendment 6: a background SHELL launched in an EARLIER turn. The job-state layer
        # above covers this only for background-JOB sessions (CLAUDE_JOB_DIR), a shell is not
        # a Monitor, and the current-turn scan below cannot see a launch from a previous
        # turn -- so an agent parked on a multi-hour run was nudged mid-wait. The launch ack
        # carries the task id, and a terminal <task-notification> for that id ends the wait
        # exactly as it does for monitors (terminal evidence still WINS).
        shell_signal = _background_shell_last_signal(events)
        if shell_signal:
            shell_now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            shell_ended = _ended_monitor_ids(events)
            if any(tid not in shell_ended
                   and shell_now - ts <= _bg_shell_stale_secs()
                   for tid, ts in shell_signal.items()):
                return True
        start = -1  # index of the last turn boundary; -1 => whole transcript in scope
        for i, ev in enumerate(events):
            if _is_human_prompt(ev) or _is_task_notification(ev):
                start = i
        # Monitor-event boundary (backstop for transcripts where the launch is not
        # visible, e.g. compaction/resume): if THIS turn was triggered by a monitor
        # progress <event>, the agent is parked waiting for the next event -- but ONLY
        # while that monitor has not been explicitly ended (TaskStop / terminal <status>
        # on any delivery path) and the boundary event itself is FRESH (staleness window;
        # no timestamp -> no suppression). Amendment 3b: a stale boundary or an ended
        # monitor must not keep suppressing.
        if start >= 0 and _is_monitor_event(events[start]):
            bev = events[start]
            bc = _event_content(bev)
            m = _TASK_NOTIFICATION_TASK_ID.search(bc) if isinstance(bc, str) else None
            tid = m.group(1) if m else None
            seen_at = _event_epoch_seconds(bev)
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            if (tid is not None and seen_at is not None
                    and tid not in _ended_monitor_ids(events)
                    and now - seen_at <= _monitor_stale_secs()):
                return True
        launched = []
        completed = set()
        for ev in events[start + 1:]:
            cid = _completion_tool_use_id(ev)
            if cid:
                completed.add(cid)
            # An inline result is proof the launch did not outlive the turn -- the only
            # discriminator left now that a missing run_in_background means background.
            completed.update(_inline_resolved_tool_use_ids(ev))
            launched.extend(_background_launch_ids(ev))
        return any(lid not in completed for lid in launched)
    except Exception:
        return False
