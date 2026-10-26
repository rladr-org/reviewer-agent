#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Transcript layer of the reviewer package: reading and classifying Claude session events.

Step 2 of LIB_SPLIT_PLAN.md. This module imports NOTHING from the package -- not even
`reviewer_base`, which the plan expected -- so it is a second independent root rather than a
layer above base. `tools/check_split_layering.py` fails if that ever stops being true.

Why a layer of its own: the manifest, the prompt/operator relay AND the stopContinue
background-wait detector all parse the same transcript. Without this layer, manifest and
stopcontinue reference each other's helpers across 18 call sites, which is a genuine cycle
rather than a tidiness complaint (LIB_SPLIT_PLAN.md 3.1).

What belongs here: locating the transcript, parsing it once per process, and the small
accessors that answer "what does this event contain" and "is this a real human turn-start".
What does not: anything that decides what to DO with those events.

Everything here is re-exported by `reviewer_lib` for back-compat, so existing
`rl._transcript_events(...)` call sites are unchanged. `_TRANSCRIPT_CACHE` is mutable state
shared through that re-export; see LIB_SPLIT_PLAN.md 5.1 and tests/test_split_shared_state.py
for why it must be MUTATED and never rebound.
"""
import datetime
import json
import os
import re
from pathlib import Path

_TASK_NOTIFICATION_TUID = re.compile(r"<tool-use-id>\s*([^<\s]+)\s*</tool-use-id>")

# Synthetic / system-injected user events that are NOT a human turn-start.
_NON_HUMAN_USER_PREFIXES = (
    "<task-notification>", "stop hook feedback:", "<local-command",
    "caveat:", "<command-", "this session is being continued",
)


# One parse per process, per path. A hook is a short-lived process, and within it several
# consumers want the transcript: the operator block, the watermark, the context diagnostic,
# and the background-wait detector. Real transcripts reach tens of MB (25MB observed), so
# re-parsing per call was the dominant cost. Caching also gives every consumer the SAME
# snapshot, which is the correct semantics -- a file still being appended to must not make
# two calls in one audit disagree.
_TRANSCRIPT_CACHE = {}


def resolve_transcript_path(data):
    """The session transcript for a hook payload. Claude Code supplies `transcript_path`;
    if some event ever omits it, fall back to the conventional projects-dir location so the
    operator relay degrades to a miss rather than going silently dark."""
    path = data.get("transcript_path")
    if path and os.path.exists(path):
        return path
    sid, cwd = data.get("session_id"), data.get("cwd")
    if not sid or not cwd:
        return path
    guess = (
        Path.home() / ".claude" / "projects"
        / os.path.realpath(str(cwd)).replace(os.sep, "-")
        / (str(sid) + ".jsonl")
    )
    return str(guess) if guess.exists() else path


def _transcript_events(transcript_path):
    """Parsed JSONL events (dicts) from a Claude transcript, or [] if unreadable."""
    key = str(transcript_path)
    if key in _TRANSCRIPT_CACHE:
        return _TRANSCRIPT_CACHE[key]
    events = _parse_transcript_events(transcript_path)
    _TRANSCRIPT_CACHE[key] = events
    return events


def _reset_transcript_cache():
    """Drop the per-process snapshot. Production never needs this -- each hook invocation is
    a fresh process -- but tests that simulate consecutive audits in ONE process do."""
    _TRANSCRIPT_CACHE.clear()


def _parse_transcript_events(transcript_path):
    try:
        with open(transcript_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except (OSError, TypeError):
        return []
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _event_content(ev):
    msg = ev.get("message")
    return msg.get("content") if isinstance(msg, dict) else None


def _event_text(ev):
    """Human-readable text of a user event, joining text blocks when the content is a list
    (what a message with a pasted image or mixed blocks looks like)."""
    c = _event_content(ev)
    if isinstance(c, list):
        c = "\n".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return c if isinstance(c, str) else ""


def _is_human_prompt(ev):
    """A genuine human turn-start: a user message (string content, OR a list of blocks
    carrying human text) that is NOT a tool_result carrier, a background ack (toolUseResult),
    a meta/compaction event, nor a system-injected synthetic (task-notification, hook
    feedback, slash-command output)."""
    if ev.get("type") != "user":
        return False
    if ev.get("isMeta") or ev.get("isCompactSummary"):
        return False
    if ev.get("toolUseResult") is not None:
        return False
    c = _event_content(ev)
    if isinstance(c, list):
        # A human prompt may be a list of blocks (text, image); a tool_result carrier is not.
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
            return False
        c = " ".join(
            b.get("text", "") for b in c
            if isinstance(b, dict) and b.get("type") == "text"
        )
    if not isinstance(c, str) or not c.strip():
        return False
    low = c.lstrip().lower()
    return not any(low.startswith(pre) for pre in _NON_HUMAN_USER_PREFIXES)


def _last_turn_prompt_text(events):
    """Text of the most recent MAIN-session turn-start user event -- the prompt that STARTED the
    current turn, human OR isMeta-injected (a watchdog tick, a stop-hook-feedback continuation).

    Unlike `_is_human_prompt` this INCLUDES `isMeta` turn-starts: a fired watchdog tick is a
    `type: user` event with `isMeta: True`, so keying tick detection on "last human prompt" would
    filter the tick out and return the previous real prompt. It still excludes `tool_result`
    carriers and, crucially, `isSidechain` (subagent) events, so a subagent's prompt later in the
    transcript is never mistaken for the current main turn. Returns '' if none is found."""
    for ev in reversed(events or []):
        if ev.get("type") != "user":
            continue
        if ev.get("isSidechain") or ev.get("isCompactSummary"):
            continue
        if ev.get("toolUseResult") is not None:
            continue
        c = _event_content(ev)
        if isinstance(c, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" for b in c
        ):
            continue
        txt = _event_text(ev)
        if txt.strip():
            return txt
    return ""


def ends_with_operator_interrupt(events):
    """True when the operator's interrupt is the last thing in the main session, ignoring the
    watchdog tick prompt that is arriving now: the operator stopped the agent and has not spoken or
    let it continue since."""
    for ev in reversed(events or []):
        kind = ev.get("type")
        if ev.get("isSidechain") or kind not in ("user", "assistant"):
            continue
        if kind == "assistant":
            msg = ev.get("message")
            if isinstance(msg, dict) and msg.get("model") == "<synthetic>":
                continue  # the host's own filler ("No response requested."), not the agent
            return False
        if _event_text(ev).lstrip().startswith("[Request interrupted by user"):
            return True
        if _is_human_prompt(ev):
            return False  # the operator spoke after the interrupt
        # Anything else is not the operator speaking -- the tick itself, a task notification, hook
        # feedback, a compaction summary, a tool-result carrier -- and leaves a pause intact.
    return False


def _queue_text(content):
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content
                            if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def _is_operator_queue_text(text):
    """True for a queued message the operator typed, not one the host injected (a watchdog tick,
    a task notification, hook feedback)."""
    if not text.strip():
        return False
    low = text.lstrip().lower()
    return not (low.startswith("stopcontinuewatchdog tick") or any(
        low.startswith(pre) for pre in _NON_HUMAN_USER_PREFIXES))


class _OperatorReplyWatch:
    """Is a message the operator submitted still waiting to be delivered? Claude Code records its
    input queue in the transcript: `enqueue` when the operator presses Enter during a running
    turn, `dequeue` when the oldest entry starts the next turn, `remove` when it is delivered
    inside the running turn, `popAll` when the queue is pulled back into the input box. Replaying
    those ops finds a pending reply however many agent messages followed it -- asking only whether
    a reply came after the agent's last message missed one submitted while the agent was still
    working (2026-10-26). Reads the transcript once, then only what is appended, so a Stop hook
    can poll it cheaply while a review runs."""

    def __init__(self, transcript_path):
        self._path = transcript_path
        self._offset = 0
        self._queue = []  # (text, typed by the operator), oldest first
        self._consume()

    def _consume(self):
        data = self._read_from(self._offset)
        end = data.rfind(b"\n") + 1  # a line still being written is read on the next poll
        self._offset += end
        for line in data[:end].splitlines():
            if b"queue-operation" not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict) and ev.get("type") == "queue-operation":
                self._apply(ev.get("operation"), _queue_text(ev.get("content")))

    def _apply(self, op, text):
        if op == "enqueue":
            self._queue.append((text, _is_operator_queue_text(text)))
        elif op == "dequeue":
            if self._queue:
                self._queue.pop(0)
        elif op == "remove":
            for i, (queued, _) in enumerate(self._queue):
                if queued == text:
                    del self._queue[i]
                    break
        elif op == "popAll":
            self._queue = []

    def _read_from(self, offset):
        if not self._path:
            return b""
        try:
            with open(self._path, "rb") as fh:
                fh.seek(offset)
                return fh.read()
        except OSError:
            return b""

    def replied(self):
        self._consume()
        return any(from_operator for _, from_operator in self._queue)


def operator_reply_watch(transcript_path):
    return _OperatorReplyWatch(transcript_path)


def directives_answered_without_action(events):
    """How many of this turn's most recent stop-hook directives the agent answered without a
    single tool call, counted back from the latest until one it acted on.

    A reply with no tool call means the agent disagreed and said so: re-blocking it cannot make it
    act, and the supervisor rewords each push, so a wording check never sees a repeat (2026-09-30:
    nine reworded pushes to make the operator's choice, each answered in text). Only the current
    turn counts: any other user turn-start (an operator message, a watchdog tick, a task
    notification) ends the chain. Sidechain (subagent) events are ignored."""
    count = 0
    acted = False
    for ev in reversed(events or []):
        if ev.get("isSidechain"):
            continue
        kind = ev.get("type")
        content = _event_content(ev)
        if kind == "assistant":
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
                acted = True
            continue
        if kind != "user" or ev.get("toolUseResult") is not None:
            continue
        if isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            continue
        if not _event_text(ev).lstrip().lower().startswith("stop hook feedback:"):
            return count  # the turn started here
        if acted:
            return count
        count += 1
    return count


def _event_epoch_seconds(ev):
    """The event's ISO-8601 timestamp as epoch seconds, or None."""
    ts = ev.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
