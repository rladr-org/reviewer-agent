#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Stop/continue layer of the reviewer package: reading the transcript's tail and deciding.

Step 8 of LIB_SPLIT_PLAN.md, and the LAST one -- after this `reviewer_lib.py` defines nothing of
its own and is purely a re-export facade. Imports `base` and `state`;
`tools/check_split_layering.py` fails if it ever reaches forward in the layer order.

Two responsibilities that share the stop hook's decision path: recovering the last assistant text
from a transcript that may still be flushing, and the nudge budget that bounds how often a
continuation may be pushed. The review history and the suggestion/required-fixes formatting live
here too, because `save_review` is what the stop hook writes on its way out.

`_sleep` is patched by `tests/test_reviewer_lib.py` on THIS module, not on `reviewer_lib`: its
caller `last_assistant_text` lives here and resolves the name in this module's globals, so a
facade patch would not reach it (LIB_SPLIT_PLAN.md 5.3). The facade still re-exports `_sleep`,
which is safe because it is a stable alias assigned once -- unlike `_LAST_INVOCATION`, which is
rebound and therefore deliberately kept private to `reviewer_backends`.

"""
import datetime
import json
import os
import sys
import time

from reviewer_base import (
    _sanitize,
    reviewer_home,
    short,
)
from reviewer_state import (
    _state_stem,
    _tunable_int,
    canonical_project_key,
    config_get,
    last_review_file,
)

# --------------------------------------------------------------------------
# stopContinue: read the agent's last message; decide if it's a user question
# --------------------------------------------------------------------------
_sleep = time.sleep  # module-level seam so tests can fake the flush-race retry delay
_delay_sleep = time.sleep  # separate seam for the stopContinue pre-enactment delay (below)


def _assistant_event_text(ev):
    """All text-block content of one assistant transcript event, joined; '' if tool-only."""
    msg = ev.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    texts = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
    return "\n".join(texts).strip()


def _is_tool_result_user_event(ev):
    """True for the user-typed events that merely carry tool results back to the model --
    those belong to the ASSISTANT'S turn and are not a turn boundary."""
    msg = ev.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    return (
        isinstance(content, list)
        and len(content) > 0
        and all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _final_turn_text(lines):
    """Concatenated text of ALL assistant events in the transcript's FINAL turn, walking
    backward until a real user message (the turn boundary). Tool-result user events and
    non-assistant events (progress, system) are part of the turn and skipped."""
    collected = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "assistant":
            text = _assistant_event_text(ev)
            if text:
                collected.append(text)
        elif etype == "user":
            if _is_tool_result_user_event(ev):
                continue
            break  # real user message -> start of the final turn
    return "\n".join(reversed(collected)).strip()


def last_assistant_text(transcript_path, retries=3, retry_delay=0.3):
    """Concatenated text of the final TURN's assistant messages in a Claude Code transcript
    (JSONL), or '' if none / unreadable / the final turn is genuinely tool-only.

    2026-07-21 hardening (premature stops were slipping through unreviewed):
    - TURN-scoped, not last-event-scoped: a turn that ends with a tool call no longer
      reads as empty when it said something earlier in the same turn.
    - Flush-race retry: the Stop event can fire before the final text event is written
      (observed: text_len=0 on a turn that ended with a long summary); on an empty read
      the reader waits briefly and re-reads, up to `retries` attempts total."""
    for attempt in range(max(1, retries)):
        try:
            with open(transcript_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, TypeError):
            return ""
        text = _final_turn_text(lines)
        if text:
            return text
        if attempt < retries - 1:
            _sleep(retry_delay)
    return ""


def is_stop_ok(codex_out):
    """True iff the stop supervisor's verdict is the STOP_OK sentinel (task genuinely done,
    or a human-only decision) -- i.e. the agent is allowed to stop. The first non-empty line,
    stripped and uppercased, must start with STOP_OK; anything else is a continue directive."""
    for line in (codex_out or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return stripped.upper().startswith("STOP_OK")
    return False


# --------------------------------------------------------------------------
# stopContinue nudge budget: bound the forced continuations per turn boundary so
# the supervisor can keep pushing the agent without ever running away.
# --------------------------------------------------------------------------
def nudge_count_file(proj, claude_session_id):
    # DELIBERATE EXCEPTION to the no-shared-slot rule the reviewer id, watermark and audited
    # stage follow: here NOT persisting is the hazard. An unidentifiable session with no
    # counter would have an unbounded nudge budget -- a runaway continuation loop -- whereas
    # sharing one budget only makes it stricter. Keep the shared session slot.
    #
    # The PROJECT half is not shared, and the asymmetry is deliberate. Sharing across sessions
    # tightens the budget; sharing across repositories would let a runaway in one repository
    # exhaust the budget that protects a different one, so a genuinely incomplete stop there
    # would go unsupervised. An unresolvable project falls back to one shared stem for exactly
    # the reason the session half does.
    key = _sanitize(claude_session_id) if claude_session_id else "default"
    stem = (_state_stem(canonical_project_key(proj)) if str(proj or "").strip() else "unscoped")
    return reviewer_home() / "state" / (stem + ".sess_" + key + ".nudges")


def read_nudge_count(proj, claude_session_id):
    f = nudge_count_file(proj, claude_session_id)
    if not f.exists():
        return 0
    try:
        return int((f.read_text() or "0").strip() or "0")
    except ValueError:
        return 0


def bump_nudge_count(proj, claude_session_id):
    n = read_nudge_count(proj, claude_session_id) + 1
    f = nudge_count_file(proj, claude_session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(n) + "\n")
    return n


def reset_nudge_count(proj, claude_session_id):
    nudge_count_file(proj, claude_session_id).unlink(missing_ok=True)


def max_nudges():
    """Cap on forced continuations per turn boundary (env REVIEWER_STOPCONTINUE_MAX_NUDGES,
    default 10, floored at 1). The supervisor stops nudging past this so a mis-judging
    reviewer can never loop the agent forever."""
    return _tunable_int("REVIEWER_STOPCONTINUE_MAX_NUDGES", "maxNudges", 10, 1)


def stopcontinue_effort():
    """Reasoning effort for the stop-supervisor call (env REVIEWER_STOPCONTINUE_EFFORT,
    default 'low'). The supervisor only judges continue-vs-done and emits a short next step,
    so it does NOT need the user's global xhigh -- keeping it low is what makes every
    turn-end snappy instead of a 30-60s xhigh resume."""
    val = os.environ.get("REVIEWER_STOPCONTINUE_EFFORT") or config_get("stopContinueEffort")
    return (str(val).strip() if val else "") or "low"


def stopcontinue_effort_config():
    return 'model_reasoning_effort="{}"'.format(stopcontinue_effort())


# --------------------------------------------------------------------------
# stopContinue pre-enactment delay: pause BEFORE enacting a supervised verdict (a continue
# directive OR a stop approval) so the operator has a window to intervene even when the model's
# call is wrong. Default 0 (no delay); the Stop hook budget-caps it so it can never overrun host
# cancellation (which would fail the stop OPEN). See docs/plans/stopcontinue_delay_and_prompt_hardening.md.
# --------------------------------------------------------------------------
DELAY_MAX_S = 300  # a single intervention window; also the ceiling the hook budget is sized for
DELAY_MARGIN_S = 30  # leave this much of the hook budget for emitting the decision after the sleep
HOOK_BUDGET_S = 630  # MUST match the Stop hook timeout in hooks/hooks.json (pinned by a test):
#                      STOP_TIMEOUT (300) + DELAY_MAX_S (300) + DELAY_MARGIN_S (30).


def stopcontinue_delay_secs():
    """Seconds to pause before enacting a stopContinue verdict (env
    REVIEWER_STOPCONTINUE_DELAY_SECS or config stopContinueDelaySecs, default 0 = no delay,
    floored at 0 and capped at DELAY_MAX_S). Applies to BOTH a continue directive and a stop
    approval."""
    return min(DELAY_MAX_S, _tunable_int("REVIEWER_STOPCONTINUE_DELAY_SECS", "stopContinueDelaySecs", 0, 0))


def apply_stopcontinue_delay(decision, budget_remaining=None):
    """Pause before enacting a stopContinue `decision` ("continue" or "stop-approval"), if
    configured. Announces on stderr BEFORE sleeping so an operator can Escape during the window,
    then sleeps via the `_delay_sleep` seam. `budget_remaining`, when given, caps the sleep against
    the time left before the host cancels the Stop hook, so the decision is always emitted."""
    secs = stopcontinue_delay_secs()
    if budget_remaining is not None:
        secs = min(secs, max(0, int(budget_remaining)))
    if secs <= 0:
        return
    try:
        sys.stderr.write(
            "[stopContinue] applying {} in {}s -- interrupt now to override.\n".format(decision, secs))
        sys.stderr.flush()
    except Exception:  # a heads-up must never be the reason the decision is not enacted
        pass
    _delay_sleep(secs)


# Retained verdicts per project. Enough to cover a long convergence across several sessions,
# small enough that the directory stays readable and bounded -- but that is a guess about how
# long a convergence runs, and this corpus is the input to the reviewer-model comparison, so the
# operator sets it: REVIEWER_REVIEW_HISTORY_KEEP, or `reviewHistoryKeep` in the config file.
REVIEW_HISTORY_KEEP = 100


def review_history_keep():
    """How many verdicts per project survive pruning. Read at CALL time, never captured at
    import: every hook is a fresh process, so an import-time capture would look correct in
    production and only ever be wrong under test. The floor is 1 -- a cap of 0 would delete the
    verdict being written in the same call."""
    return _tunable_int("REVIEWER_REVIEW_HISTORY_KEEP", "reviewHistoryKeep",
                        REVIEW_HISTORY_KEEP, 1)


def review_history_dir(proj):
    return reviewer_home() / "reviews" / (
        _state_stem(canonical_project_key(proj)) + ".history")


def review_history(proj):
    """Retained verdicts for `proj`, oldest first. Per PROJECT, not per session: this is read to
    see how a review converged across attempts, and attempts span sessions."""
    d = review_history_dir(proj)
    try:
        return sorted(p for p in d.iterdir() if p.is_file())
    except OSError:
        return []


def _append_review_history(proj, text, claude_session_id=None):
    d = review_history_dir(proj)
    d.mkdir(parents=True, exist_ok=True)
    # Sorted by NAME == sorted by time, via a zero-padded counter one past the current newest.
    # A timestamp alone cannot order two verdicts written inside the same second; reviews take
    # minutes in practice, but that is a habit, not a guarantee. The stamp rides along as
    # readable metadata only.
    existing = sorted(p for p in d.iterdir() if p.is_file())
    try:
        nxt = int(existing[-1].name.split("-", 1)[0]) + 1
    except (ValueError, IndexError):
        nxt = len(existing) + 1
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sid = _sanitize(str(claude_session_id or "").strip()) or "unknown"
    (d / "{:06d}-{}-{}.txt".format(nxt, stamp, sid)).write_text(text, encoding="utf-8")
    for stale in existing[:max(0, len(existing) + 1 - review_history_keep())]:
        stale.unlink(missing_ok=True)


# Terminal outcomes that carry NO verdict. Each is a real event in a project's review
# history -- a blocked commit an operator will ask about later -- but none of them may touch
# the latest-ruling file: that file is shown to the next reviewer as "your previous verdict",
# so writing "TIMEOUT" into it would both forge a ruling and destroy the findings the retry
# exists to reuse.
OUTCOME_TIMEOUT = "TIMEOUT"
OUTCOME_CANCELLED = "CANCELLED"
OUTCOME_EMPTY = "EMPTY"
OUTCOME_FAILED = "FAILED"


def record_review_outcome(proj, claude_session_id=None, outcome=OUTCOME_FAILED, detail="",
                          backend=None):
    """Append one labelled non-verdict outcome to this project's retained history.

    Returns the history path, or None when nothing was written. Never raises: the gate is
    fail-closed, and a failure to record must not be the reason a commit is blocked or allowed.
    """
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = (
        "OUTCOME: {}\n"
        "when: {}\n"
        "backend: {}\n"
        "session: {}\n"
        "\n"
        "No verdict was produced. The saved latest ruling for this project, if any, is from an "
        "earlier review and was deliberately left untouched.\n"
        "\n"
        "detail:\n{}\n"
    ).format(
        outcome, stamp, backend or "unknown",
        str(claude_session_id or "unidentified"), (detail or "").strip() or "(none)",
    )
    try:
        _append_review_history(proj, body, claude_session_id)
    except OSError:
        return None
    d = review_history_dir(proj)
    try:
        return str(sorted(p for p in d.iterdir() if p.is_file())[-1])
    except (OSError, IndexError):
        return None


def clear_review_history(proj, claude_session_id=None):
    """Delete this project's retained history AND its saved rulings; return the paths removed.

    Both, because both are the same kind of data -- full verdicts and the diffs they quote, in
    plaintext, on local disk -- and an operator asking to purge review history does not mean
    "leave the most recent one behind". Scoped to one canonical project root: reviews of other
    repositories are not this command's to delete.
    """
    removed = []
    directory = review_history_dir(proj)
    try:
        entries = sorted(p for p in directory.iterdir() if p.is_file())
    except OSError:
        entries = []
    for path in entries:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    try:
        directory.rmdir()
    except OSError:
        pass
    # Saved rulings are "<project-stem>[.<session>].last_review.txt"; match the project half so
    # every session's ruling for THIS project is purged and no other project's is.
    stem = _state_stem(canonical_project_key(proj))
    reviews = reviewer_home() / "reviews"
    try:
        candidates = sorted(
            p for p in reviews.iterdir()
            if p.is_file() and p.name.startswith(stem) and p.name.endswith(".last_review.txt")
        )
    except OSError:
        candidates = []
    for path in candidates:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(str(path))
    return removed


def save_review(text, proj, claude_session_id=None, history=False):
    """Persist a commit-review verdict so the complete list survives even if a very long denial
    reason is capped. Returns the path required_fixes_message reports to the agent.

    `history` additionally appends to this project's retained history. The caller passes it only
    for a COMPLETED audit that returned a verdict: a timeout or crash yields empty or partial
    output, and writing that would destroy the findings the retry exists to reuse.

    Never raises: the gate is fail-closed, so a failure to write a log must not block a commit."""
    p = last_review_file(proj, claude_session_id)
    body = text or ""
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        if history:
            _append_review_history(proj, body, claude_session_id)
    except OSError:
        pass
    return str(p)


_SUGGESTIONS_MARKER = "SUGGESTIONS:"


def _split_suggestions(out):
    """(required, suggestions) or (out, None) when there is no usable suggestions block.

    Fail-closed on ambiguity. A malformed or REPEATED marker returns (out, None), so the
    whole text is presented as required rather than a real defect being silently demoted
    to a non-blocking suggestion. The gate errs toward too much being required, never
    toward too little.
    """
    text = (out or "").strip()
    # Only a marker at the START of a line delimits the block; the word may legitimately
    # appear inside a finding ("put it in SUGGESTIONS").
    starts = [i for i, line in enumerate(text.splitlines())
              if line.strip().startswith(_SUGGESTIONS_MARKER)]
    if len(starts) != 1:
        return text, None
    lines = text.splitlines()
    idx = starts[0]
    required = "\n".join(lines[:idx]).strip()
    suggestions = "\n".join(lines[idx:]).strip()
    if not required or not suggestions:
        return text, None
    return required, suggestions


def required_fixes_message(codex_out, review_path):
    """Build the (reason, systemMessage) for a REJECTED audit. The FULL Codex output
    is relayed in the reason (never truncated) so Claude sees every required fix;
    review_path is the on-disk backup of the complete text."""
    out = (codex_out or "").strip()
    required, suggestions = _split_suggestions(out)
    if suggestions is None:
        # No suggestions block: byte-identical to the pre-2026-07-31 wording, so the
        # common path is provably unaffected by this change.
        reason = (
            "reviewerAgent did NOT approve the staged changes and the commit was "
            "blocked. Treat the full text below as the REQUIRED FIXES before this commit "
            "can proceed; address every item, then stage and attempt the commit again (it "
            "will be re-reviewed). The complete list is also saved at {}:\n\n{}".format(
                review_path, out
            )
        )
    else:
        # Label the two halves distinctly. The relay already passed the full text through,
        # so suggestions arrived intact -- but under a heading calling everything a REQUIRED
        # FIX, which would reintroduce in the relay the exact problem the prompt change
        # fixes: non-defects read as blocking.
        reason = (
            "reviewerAgent did NOT approve the staged changes and the commit was "
            "blocked. The REQUIRED FIXES below must be addressed before this commit can "
            "proceed; address every item, then stage and attempt the commit again (it "
            "will be re-reviewed). The reviewer also returned SUGGESTIONS, which are "
            "NON-BLOCKING: they are not required for this commit and may be deferred or "
            "declined. The complete text is also saved at {}:\n\n"
            "--- REQUIRED FIXES (blocking) ---\n{}\n\n"
            "--- SUGGESTIONS (non-blocking, not required for this commit) ---\n{}".format(
                review_path, required, suggestions
            )
        )
    # Echo the findings to the terminal too (the operator otherwise never sees WHAT was
    # required) -- bounded higher than the default because fix lists run long, with the
    # on-disk path carrying the complete text.
    msg = (
        "[reviewerAgent] git commit -> the reviewer returned required fixes "
        "(commit blocked):\n{}\n(full review: {})".format(short(out, 1500), review_path)
    )
    return reason, msg
