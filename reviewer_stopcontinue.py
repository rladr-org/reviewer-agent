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
import difflib
import json
import os
import re
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


def _ends_with_text(lines):
    """True when the newest assistant-side event carries text, i.e. the closing message has
    landed. A trailing thinking/tool_use event, or a tool result after the last text, means the
    turn's final text may still be in flight."""
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
        if ev.get("type") == "assistant":
            return bool(_assistant_event_text(ev))
        if ev.get("type") == "user":
            return False
    return False


def last_assistant_text(transcript_path, retries=3, retry_delay=0.3, final_message=None):
    """Concatenated text of the final TURN's assistant messages in a Claude Code transcript
    (JSONL), or '' if none / unreadable / the final turn is genuinely tool-only.

    - TURN-scoped, not last-event-scoped (2026-07-21): a turn that ends with a tool call no
      longer reads as empty when it said something earlier in the same turn.
    - Flush race: the Stop event can fire before the closing text is written. "Non-empty" is not
      "complete" (2026-09-24: the supervisor judged a turn by its opening paragraph and ordered
      finished work redone). When Claude Code supplies `last_assistant_message` in the Stop
      payload (`final_message`), the read is settled once the transcript ENDS with that text.
      Without it, the read is settled once the newest assistant event carries text. Otherwise
      wait and re-read, up to `retries` attempts, then append the payload's final message if it
      never landed. A SUPPLIED but empty `final_message` says the closing message carried no
      text, so a transcript ending in text is stale by definition: it never settles early, and
      the reader waits out its retries before returning what the turn said."""
    supplied = final_message is not None
    final = (final_message or "").strip()
    text = ""
    for attempt in range(max(1, retries)):
        try:
            with open(transcript_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, TypeError):
            return final
        text = _final_turn_text(lines)
        # A TERMINAL match, not membership: the final phrase may also have appeared earlier in
        # the turn while the transcript still ends with stale output.
        if final:
            settled = text.rstrip().endswith(final)
        else:
            settled = not supplied and bool(text) and _ends_with_text(lines)
        if settled:
            return text
        if attempt < retries - 1:
            _sleep(retry_delay)
    if final and not text.rstrip().endswith(final):
        return (text + "\n" + final).strip()
    return text


# The sentinel as a whole line: STOP_OK alone, tolerating only markdown emphasis / backticks
# around it and trailing sentence punctuation (`**STOP_OK**`, `` `STOP_OK` ``, `STOP_OK.`).
# Deliberately exact-token, NOT a prefix: a continue directive can end with a sentence that
# merely mentions the sentinel ("STOP_OK is not warranted until the tests pass"), and a prefix
# rule on the trailing line would read that as approval. Prefix matching stays confined to the
# first line, where the prompt puts the verdict and where the long-standing tests pin it.
_STOP_OK_BARE_LINE = re.compile(r"^[\s*_`]*STOP_OK[\s*_`.!]*$")
# The last line may also END with the sentinel as its own sentence, after a full stop, `!` or `?`
# (observed 2026-09-29: "...it only offers an unrequested code fix. STOP_OK", relayed as a
# keep-working directive). A directive that ends by naming it ("then reply STOP_OK", "first run
# the tests; then reply: STOP_OK") is not: a colon or semicolon does not end the directive.
_STOP_OK_FINAL_SENTENCE = re.compile(r"(?:^|[.!?]\s+)[*_`]*STOP_OK[\s*_`.!]*$")
# The first line opens with the WHOLE WORD STOP_OK: a trailing clause is fine (`STOP_OK -- the
# task is genuinely complete.`), a longer token is not (`STOP_OKAY`). A bare `startswith` accepted
# the latter, in conflict with the exact-token rule the Codex parser and the trailing line apply.
_STOP_OK_OPENING = re.compile(r"^STOP_OK\b")
# The commit gate's approval word, answered by a supervisor that took itself for the auditor.
_GATE_APPROVAL_OPENING = re.compile(r"^[\s*_`]*APPROVED\b")


def stop_ok_position(codex_out):
    """Where the STOP_OK sentinel sits in the stop supervisor's reply, or None.

    "first" -- the first non-empty line opens with the whole word STOP_OK (the prompt-compliant
               shape; a trailing clause such as `STOP_OK -- the task is genuinely complete.` is
               fine, `STOP_OKAY` is not).
    "last"  -- the last non-empty line is the bare sentinel, or ends with it as its own final
               sentence (`_STOP_OK_FINAL_SENTENCE`): a model that explained itself and THEN answered. Observed in the wild (2026-09, claude backend):
               first-line-only parsing read this permit-the-stop verdict as a continue directive
               and nudged a finished task to the cap, relaying reasoning that said there was
               nothing to do as the "keep working" instruction.
    "mid"   -- a bare sentinel line exists that is neither first nor last, i.e. the reply both
               permits the stop and keeps directing. Contradictory; NOT honored (the supervisor's
               job is to keep the agent working, so the ambiguous reading resolves to continue)
               but reported distinctly so the stop hook can log it instead of silently inverting
               it. The stop-path analogue of the commit gate's APPROVED-and-REJECTED refusal.
    "gate"  -- the first non-empty line opens with the whole word APPROVED: the commit gate's
               approval, not a stop verdict (2026-09-26: framed as the commit auditor, the
               supervisor answered APPROVED and it was relayed as a keep-working directive). It
               neither permits the stop as a completion nor directs any work; the stop hook
               allows the stop and says why. A REJECTED reply stays a directive: it carries
               findings the agent can act on.
    None    -- no sentinel in an honored-or-contradictory position: a continue directive, or
               empty / whitespace-only / None input.

    See docs/plans/stopcontinue_trailing_stop_ok.md."""
    lines = [ln.strip() for ln in (codex_out or "").splitlines() if ln.strip()]
    if not lines:
        return None
    if _STOP_OK_OPENING.match(lines[0].upper()):
        return "first"
    if _GATE_APPROVAL_OPENING.match(lines[0].upper()):
        return "gate"
    if _STOP_OK_FINAL_SENTENCE.search(lines[-1].upper()):
        return "last"
    if any(_STOP_OK_BARE_LINE.match(ln.upper()) for ln in lines[1:-1]):
        return "mid"
    return None


def is_stop_ok(codex_out):
    """True iff the stop supervisor's verdict is the STOP_OK sentinel (task genuinely done,
    or a human-only decision) -- i.e. the agent is allowed to stop. The sentinel counts as the
    opening word of the FIRST non-empty line or as the bare token on the LAST one; a mid-reply
    sentinel is contradictory and does not count. Anything else is a continue directive.
    `stop_ok_position` is the single classifier; this is its boolean view."""
    return stop_ok_position(codex_out) in ("first", "last")


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
    default 8, floored at 1). The supervisor stops nudging past this so a mis-judging
    reviewer can never loop the agent forever. The default stays below Claude Code's own limit
    of 9 consecutive Stop-hook blocks: at 10 the host always ended the loop first, with an
    error, and this cap never fired."""
    return _tunable_int("REVIEWER_STOPCONTINUE_MAX_NUDGES", "maxNudges", 8, 1)


# The prompt's own generic continuation. It is identical every time BY DESIGN, so it can never
# count as a repeat.
GENERIC_CONTINUE = ("You said that there is more work to do, and it does not appear to require "
                    "additional operator approval. In that case, continue your work.")


def _normalize_directive(text):
    return " ".join((text or "").lower().split())


def last_directive_file(proj, claude_session_id):
    return nudge_count_file(proj, claude_session_id).with_suffix(".last_directive")


# Words that do not change what a directive asks for.
_FILLER_WORDS = frozenset(
    "a an the and or to then of for in on at it its this that these those is are be please "
    "now also just so".split())


def _content_words(normalized):
    """The directive's content words IN ORDER: order carries meaning ("A before B")."""
    words = (w.strip(".,;:!?()[]'\"`-/") for w in normalized.split())
    return [w for w in words if w and w not in _FILLER_WORDS]


# Asked in a FRESH session once the agent has answered the supervisor's directives without acting.
# It sees the agent's reply and nothing else. Not the directives: the resumed supervisor cited its
# own earlier ones as the operator's decision (2026-09-30). Not the operator's message either:
# measured live on that loop, "if they are in HANDOVER.md they are approved" made this check call
# a genuine port-or-document question NOT_WAITING, while the reply alone was WAITING (2 of 2).
# One narrow question, one word back.
WAITING_CHECK_PROMPT = """\
Answer one question about a coding agent's message to its operator, with exactly one word.

WAITING: the message asks the operator for a decision, an authorization, credentials or
information that only the operator can give, and the agent cannot proceed until it has it.

NOT_WAITING: anything else. For example, the agent promises or describes work it can do itself;
it offers to continue ("shall I?", "anything else?"); or it asks something it could settle itself.

----- THE AGENT'S MESSAGE -----
{reply}
----- END -----

Reply WAITING or NOT_WAITING, and nothing else."""


def waiting_check_prompt(reply_text, limit=6000):
    return WAITING_CHECK_PROMPT.format(reply=(reply_text or "")[-limit:])


def is_waiting_verdict(reply):
    """True only for a reply whose first word is WAITING (markdown and punctuation ignored).
    Anything else, including NOT_WAITING, an empty reply or prose, keeps supervision on."""
    words = (reply or "").split()
    return bool(words) and re.sub(r"[^A-Z_]", "", words[0].upper()) == "WAITING"


def directive_repeats(proj, claude_session_id, directive, threshold=0.85):
    """True when `directive` restates the previous continue directive of this stop chain.
    Re-issuing it cannot help: the agent already answered it (2026-09-24: three identical "ask
    the operator" directives for a message that already asked).

    A restatement must be close in wording AND carry the same content words in the same order.
    Similarity alone is not enough: "Run test A and report" and "Run test B and report" score
    about 0.97, and "A before B" against "B before A" about 0.92, yet each second directive asks
    for different work, and treating it as a repeat would let the agent stop with that work
    undone."""
    now = _normalize_directive(directive)
    if not now or now == _normalize_directive(GENERIC_CONTINUE):
        return False
    try:
        prev = last_directive_file(proj, claude_session_id).read_text(encoding="utf-8")
    except OSError:
        return False
    return (bool(prev) and _content_words(prev) == _content_words(now)
            and difflib.SequenceMatcher(None, prev, now).ratio() >= threshold)


def record_directive(proj, claude_session_id, directive):
    f = last_directive_file(proj, claude_session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_normalize_directive(directive), encoding="utf-8")


def clear_directive(proj, claude_session_id):
    try:
        last_directive_file(proj, claude_session_id).unlink()
    except FileNotFoundError:
        pass


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
