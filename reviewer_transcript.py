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


def _event_epoch_seconds(ev):
    """The event's ISO-8601 timestamp as epoch seconds, or None."""
    ts = ev.get("timestamp")
    if not isinstance(ts, str):
        return None
    try:
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
