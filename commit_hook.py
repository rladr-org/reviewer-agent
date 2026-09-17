#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""PreToolUse hook for Bash, acting only on git commits.

If the reviewer is active for this project AND the Bash command is a git commit,
hand the staged changes to the Codex reviewer session for an AUDIT under the
`workspace-write` sandbox: the working tree and tmp are writable so Codex can run
the test suite, but `.git` is read-only so Codex cannot commit. Codex emits a
verdict only -- APPROVED or a REJECTED fix list.

  APPROVED        -> on the CLAUDE backend the gate performs the reviewed commit ITSELF
                     (see reviewer_commit_txn) and DENIES the tool call, naming the published
                     commit id, so the agent's own command never runs a second time; on the
                     codex/pi backends it ALLOWS Claude's own `git commit` to proceed
                     (unsandboxed).
  REJECTED        -> DENY and relay the full required-fixes list.
  Codex failure   -> DENY (fail CLOSED) and surface a visible ERROR.

The reviewer session is resolved BY ID (captured at Claude session start, or
lazily briefed here), so it works identically from the main checkout or any
worktree; the audit runs with `-C <cwd>` so it sees that worktree's own staged
diff. The exchange is echoed (systemMessage) and logged.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_audit as ra  # noqa: E402
import reviewer_commit_txn as rct  # noqa: E402
import reviewer_lib as rl  # noqa: E402

# Resolved per invocation from rl.commit_review_timeout() (default 900s, raised from 600s
# after a ~5700-line staged diff timed out): env > /reviewerModel state > config.json. Read at
# call time, not import time, so `/reviewerModel timeout <secs>` takes effect on the very next
# commit without restarting anything.

# pi's depth knob is its own: it takes `thinking`, NOT codex's model_reasoning_effort. Both the
# CALL and the LABEL below read the SAME resolver, because those two drifting apart is exactly how
# a pi review gets recorded as `max` and silently corrupts an effort comparison. This used to be a
# module constant pinned at "medium"; it is now configurable per backend via /reviewerModel effort.
def _effort_label(backend):
    """What depth setting this review actually ran at, per backend.

    Not a cosmetic field: it is the independent variable for comparing reviewer cost and catch
    rate, so a wrong value is worse than none. codex renders `cli-default` when no override is
    set -- an unset override passes no `-c model_reasoning_effort` at all, so a bare null could
    not be told apart from "unknown" once the CLI default changed underneath it. pi has no such
    case: the flag is always sent, so its resolver always yields a concrete level."""
    if backend == "pi":
        return "pi-thinking:" + rl.reviewer_effort("pi")
    if backend == "claude":
        # Always concrete, like pi's: the `--effort` flag is sent on every claude turn, so
        # there is no unset case that would have to render as `cli-default`.
        return "claude-effort:" + rl.reviewer_effort("claude")
    return rl.codex_effort() or "cli-default"


def main():
    """Own the commit transaction's lifetime: whatever `_run` opens, clean up on every exit
    path. The private snapshot lives inside .git and must not outlive the attempt."""
    state = {}
    try:
        return _run(state)
    finally:
        transaction = state.get("txn")
        if transaction is not None:
            try:
                transaction.__exit__(None, None, None)
            except Exception:
                pass


def _run(state):
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}

    # Cheap precision check FIRST so the canonical-root resolution (a git subprocess)
    # runs only for actual commits, never on every Bash command.
    command = data.get("tool_input", {}).get("command", "")
    if not rl.is_git_commit(command):
        return 0

    raw_cwd = data.get("cwd") or rl.project_dir()
    # A `git -C <target> commit` whose target is a shell variable / command substitution the gate
    # cannot resolve: it cannot know which repository the commit targets. Fail CLOSED when the
    # reviewer is active for ANY project -- an unexpandable $VAR may point at one of those active
    # repos, and gating the session cwd instead would review the WRONG repo (a fail-open). The
    # session cwd's own activation does not matter (an inactive session can still target an active
    # repo). Stay out of the way only when the reviewer is off everywhere (off means off).
    if rl.commit_c_unresolvable(command) and rl.any_reviewer_active():
        reason = (
            "reviewerAgent cannot determine which repository this commit targets: its `git -C` "
            "path is a shell variable or command substitution the gate cannot resolve, so it "
            "cannot review the correct staged set. Re-run from the repository directory as a plain "
            "`git commit`, or pass a literal path (`git -C /absolute/path commit`)."
        )
        rl.log("[commit] key={} unresolvable -C target cmd={!r} -> denied (fail-closed)".format(
            rl.canonical_project_key(raw_cwd), rl.short(command, 120)))
        print(json.dumps(rl.deny_output(
            reason,
            "[reviewerAgent] git commit blocked: the `-C` target is a shell variable the gate "
            "cannot resolve, so it cannot confirm the target repository. Use a literal path or "
            "commit from the repo directory.",
        )))
        return 0
    cwd = rl.git_c_effective_dir(command, raw_cwd)  # honor `git -C <path>`: gate the commit's repo
    if not rl.is_active(cwd):  # resolves cwd -> canonical project root internally
        return 0
    claude_sid = rl.claude_session_key(data)
    key = rl.canonical_project_key(cwd)
    retry_audit = rl.commit_retry_pending(cwd, claude_sid)
    # The rotation carryover (a retired predecessor's preserved findings) is delivered HERE, not in
    # staged_change_block, so the marker can be consumed only after a review that actually delivered
    # it. Marker-gated + non-consuming read: "" when not armed or the verdict read fails (the marker
    # then stays armed for the retry). Prepended to each backend's prompt below. The generation token
    # is read FIRST so consumption targets exactly the generation this review delivered -- a rotation
    # that re-arms a newer marker mid-review is then left for its own successor, not unlinked here.
    carry_gen = rl.commit_carryover_generation(cwd, claude_sid)
    carry_block = rl.carryover_findings_block(cwd, claude_sid)

    if rl.commits_beyond_index(command):
        reason = (
            "reviewerAgent audits ONLY the staged changes (git diff --cached), but this "
            "commit reaches BEYOND the staged set -- e.g. -a/--all or an explicit "
            "pathspec also commit working-tree changes the audit cannot see. Stage "
            "exactly what you want committed (git add <files>), then run a plain "
            "`git commit -m \"...\"` (no -a, no pathspec) so the committed set equals the "
            "audited set."
        )
        rl.log("[commit] key={} beyond_index cmd={!r} -> denied".format(key, rl.short(command, 120)))
        print(
            json.dumps(
                rl.deny_output(
                    reason,
                    "[reviewerAgent] git commit blocked: reaches beyond the staged set "
                    "(use git add + a plain git commit).",
                )
            )
        )
        return 0

    staged = rl.staged_files(cwd)
    if not staged:
        reason = (
            "reviewerAgent: nothing is staged, so there is nothing to review or commit. "
            "Stage the change(s) you want to commit (e.g. `git add <files>`), then run the "
            "commit again -- the reviewer audits and commits ONLY the staged set."
        )
        rl.log("[commit] key={} nothing_staged -> asked Claude to stage".format(key))
        print(
            json.dumps(
                rl.deny_output(reason, "[reviewerAgent] git commit blocked: nothing staged.")
            )
        )
        return 0

    # reviewerAgent-ignore.md: parse before any backend runs so a malformed staged file
    # fails-closed without consuming a review turn. See docs/plans/reviewer_ignore_guidance.md.
    ignore_matcher, ignore_prose, ignore_err = rl.parse_reviewer_ignore(cwd, staged)
    if ignore_err:
        reason = (
            "reviewerAgent: {} — fix or remove it and try again.".format(ignore_err)
        )
        rl.log("[commit] key={} reviewer_ignore parse FAILED: {} -> denied".format(
            key, rl.short(ignore_err, 200)))
        print(json.dumps(rl.deny_output(
            reason, "[reviewerAgent] commit blocked: {}".format(rl.short(ignore_err, 200))
        )))
        return 0
    # `_ignore_matcher` is passed to `staged_change_block` on the codex retry path (Channel B):
    # matched paths are dropped from the delta candidate set so their diff bytes never reach
    # the retry prompt. Non-codex branches get Channel A only. `channel_a_block` is per-backend
    # below (codex/pi/claude have different fetch capabilities).

    # Operator context (their own typed words + any /reviewerDirective) rides on the PROMPT,
    # not the brief -- the brief was read once when the reviewer session was created, so it
    # cannot carry anything the operator said since.
    transcript_path = rl.resolve_transcript_path(data)

    backend = rl.reviewer_backend(cwd)
    # Set only on the claude path, where the gate performs the commit itself; read back at the
    # approval branch. Other backends still let the agent's own command run, so it stays None.
    commit_request = None

    def _record_cancellation():
        # Called from the signal handler in reviewer_backends._run, after the reviewer tree has
        # been torn down and before this process re-raises. A cancelled review is a real event
        # in this project's history -- an operator who pressed Escape mid-audit will look for
        # it -- and it is the one outcome no later code path can record, because there is no
        # later code path.
        rl.record_review_outcome(
            cwd, claude_session_id=claude_sid, outcome=rl.OUTCOME_CANCELLED,
            detail="the review was cancelled before it returned a verdict",
            backend=backend,
        )

    if backend == "claude":
        # The gate performs the reviewed commit itself and then DENIES the tool call, so the call
        # must be exactly one plain `git commit`: anything else in it would be dropped unrun.
        # Refuse an unsupported shape BEFORE spending a review. The message and the carried flags
        # are recovered here. See docs/plans/claude_commit_transaction_design.md.
        commit_request, parse_reason = rl.parse_commit_request(command)
        if commit_request is None:
            rl.log("[commit] key={} unsupported commit shape ({}) cmd={!r} -> denied".format(
                key, rl.short(parse_reason, 200), rl.short(command, 120)))
            print(json.dumps(rl.deny_output(
                "reviewerAgent performs the reviewed commit itself, so it accepts exactly one "
                "plain `git commit` and nothing else: {}.".format(parse_reason),
                "[reviewerAgent] git commit blocked before review: {}".format(
                    rl.short(parse_reason, 200)),
            )))
            return 0
        # Capture the private snapshot BEFORE the review, so the reviewer judges exactly what
        # publication will verify, and so a too-old Git, a non-files ref format or an in-progress
        # merge/rebase fails closed here without spending a review.
        try:
            transaction = rct.CommitTransaction(cwd)
            transaction.__enter__()
        except rct.CommitError as exc:
            rl.log("[commit] key={} capture refused ({}) -> denied".format(
                key, rl.short(str(exc), 200)))
            print(json.dumps(rl.deny_output(
                "reviewerAgent could not capture the staged snapshot to review and commit it: "
                "{}. Nothing was committed and your changes are intact.".format(exc),
                "[reviewerAgent] git commit blocked: {}".format(rl.short(str(exc), 200)),
            )))
            return 0
        state["txn"] = transaction
        # Claude backend: a Claude Code child with read and search tools and nothing else. It
        # has no shell and no git tool -- and its Read sees the WORKING TREE, not the index --
        # so the staged patch cannot be fetched by the reviewer and is handed to it instead,
        # generated from the CAPTURED index so it is exactly the tree publication will verify.
        patch, truncated = transaction.patch(rl.staged_patch_max())
        if truncated or not patch.strip():
            # Fail CLOSED on a patch that could not be handed over IN FULL. A reviewer that saw
            # half a diff must never be able to approve the whole of it, which is the same rule
            # the pi backend applies to a reviewer that never read the patch at all.
            detail = (
                "it exceeds the {}-byte prompt budget".format(rl.staged_patch_max())
                if truncated else "the staged diff came back empty"
            )
            rl.record_review_outcome(
                cwd, claude_session_id=claude_sid, outcome=rl.OUTCOME_FAILED,
                detail="the staged patch could not be handed to the reviewer: " + detail,
                backend=backend,
            )
            reason = (
                "reviewerAgent: the staged patch could not be handed to the claude reviewer "
                "({}). That backend has no shell and no git tool, so it can only judge a patch "
                "it is given in full, and a partial patch must never produce an approval. The "
                "commit is blocked (fail-closed). Stage this change in smaller commits, raise "
                "the budget with REVIEWER_STAGED_PATCH_MAX, or use a backend whose reviewer "
                "reads the diff itself.".format(detail)
            )
            rl.log("[commit] key={} claude patch unavailable ({}) -> denied".format(key, detail))
            print(json.dumps(rl.deny_output(
                reason,
                "[reviewerAgent] ERROR: the staged patch could not be handed to the claude "
                "reviewer ({}). Commit blocked.".format(detail),
            )))
            return 0
        # Same placeholder discipline as pi: on a first turn the id is minted inside
        # run_claude_review, so nothing may be keyed on it until it comes back real.
        sid = "claude:" + (rl.read_claude_session(cwd, claude_sid) or "new")
        change_block = carry_block + rl.staged_change_block(cwd, claude_sid, sid)
        prompt = (
            change_block
            + "STAGED PATCH -- the complete `git diff --cached` for this commit:\n"
            + "```diff\n" + patch + "\n```\n\n"
            + rl.apply_context(rl.CLAUDE_COMMIT_PROMPT, cwd, transcript_path, claude_sid,
                               commit_audit=True)
            + rl.channel_a_block(ignore_prose, backend="claude")
        )
        rl.reset_last_invocation()
        code, out, err = rl.run_claude_review(
            cwd, claude_sid, prompt,
            timeout=rl.commit_review_timeout(),
            effort=rl.reviewer_effort("claude"),
            on_cancel=_record_cancellation,
        )
        real = rl.read_claude_session(cwd, claude_sid)
        session_known = bool(real)
        sid = "claude:" + (real or "unestablished")
    elif backend == "pi":
        # Pi backend: hermetic read-only review of the STAGED patch via the git_read tool.
        # No separate briefing turn (the brief is an appended system prompt) and no
        # workspace-write sandbox. Static audit only -- NOT because pi lacks a shell, but
        # because PI_READ_ONLY_TOOLS deliberately omits bash (see reviewer_backends).
        sid = "pi:" + (rl.read_pi_session(cwd, claude_sid) or "new")
        # Kept in its own name so the audit can record WHAT annotation was sent, not just the
        # hash of the assembled whole (DELTA_INLINE_PLAN.md Fix 2).
        change_block = carry_block + rl.staged_change_block(cwd, claude_sid, sid)
        prompt = (
            change_block
            + rl.apply_context(rl.PI_COMMIT_PROMPT, cwd, transcript_path, claude_sid,
                               commit_audit=True)
            + rl.channel_a_block(ignore_prose, backend="pi")
        )
        rl.reset_last_invocation()
        code, out, err = rl.run_pi_review(
            cwd, claude_sid, prompt,
            timeout=rl.commit_review_timeout(),
            thinking=rl.reviewer_effort("pi"),
            on_cancel=_record_cancellation,
        )
        # pi learns its session id from the RESPONSE, so the id above is the "pi:new"
        # placeholder on a first turn and run_pi_review has since persisted the real one.
        # Re-resolve before anything is keyed on it: the audit cursor carries this session's
        # round and cumulative token series, and record_audited_stage keys the NEXT turn's
        # CHANGED annotation on it, which a placeholder would make look like a different
        # reviewer.
        #
        # A turn can return a perfectly good verdict and still yield no header, in which case NO
        # session was established and the next commit is a FRESH reviewer turn. Any placeholder
        # stable enough to key state on would invent continuity that does not exist -- and the
        # damage is not a wrong round number but a "restate the findings you raised" block sent
        # to a reviewer holding no prior context, which leaves unchanged files unreviewed. So
        # per-session state is neither read nor written until a real id exists.
        real = rl.read_pi_session(cwd, claude_sid)
        session_known = bool(real)
        sid = "pi:" + (real or "unestablished")
    else:
        sid, serr = rl.ensure_session(cwd, claude_sid, on_cancel=_record_cancellation)
        if sid is None:
            # A refused model fails at the BRIEF, before any session exists, so this path sees
            # the provider's sentence first -- and reported it as a generic startup failure,
            # which is the one place the diagnosis is most needed: nothing has been established
            # yet, so every later attempt fails identically.
            diagnosis = (
                rl.model_rejection_message(backend, serr)
                if rl.model_rejection_detail(backend, serr) else ""
            )
            # A reviewer that never started still blocked this commit, so the history has to
            # say so; without this the only surviving trace was the log line.
            rl.record_review_outcome(
                cwd, claude_session_id=claude_sid, outcome=rl.OUTCOME_FAILED,
                detail="could not establish a reviewer session: {}{}".format(
                    serr, ("\n\n" + diagnosis) if diagnosis else ""),
                backend=backend,
            )
            reason = (
                "reviewerAgent is ON but could NOT establish a reviewer session ({}). "
                "The commit was blocked (fail-closed). Fix the reviewer or run "
                "/reviewerAgent off.{}".format(serr, ("\n\n" + diagnosis) if diagnosis else "")
            )
            msg = (
                "[reviewerAgent] ERROR: could not start the reviewer ({}). Commit "
                "blocked.{}".format(
                    rl.short(serr, 200), ("\n" + diagnosis) if diagnosis else "")
            )
            rl.log("[commit] key={} ensure_session FAILED: {} -> denied".format(key, serr))
            print(json.dumps(rl.deny_output(reason, msg)))
            return 0
        session_known = True  # ensure_session returns a real id or the hook already denied
        change_block = carry_block + rl.staged_change_block(
            cwd, claude_sid, sid, ignore_matcher=ignore_matcher
        )
        prompt = (
            change_block
            + rl.apply_context(rl.COMMIT_PROMPT, cwd, transcript_path, claude_sid,
                               commit_audit=True)
            + rl.channel_a_block(ignore_prose, backend="codex")
        )
        # AFTER ensure_session: briefing is its own `_run`, and without the reset the audit
        # would be recorded with the brief's argv and duration.
        rl.reset_last_invocation()
        # Per-generation scratch dir for THIS reviewer session: TMPDIR is scoped to it so the
        # reviewer's tree-copies land where rotation/orphan-sweep can reclaim them.
        scratch_dir = rl.commit_scratch_dir(sid, create=True)
        # WARN-ONLY disk reporting: never blocks a commit and is strictly inert to the verdict.
        disk_warning = rl.low_disk_warning()
        if disk_warning:
            rl.log(disk_warning)
            sys.stderr.write(disk_warning + "\n")
        # Streaming, schema-constrained audit: codex emits a --json event stream (watched for the
        # N1/N2 idle thresholds) and a strict JSON verdict, which the call reconstructs into the
        # canonical APPROVED/REJECTED text -- so `is_approved`/`completed` below are unchanged. A
        # hung reviewer is killed and retried once; a malformed verdict fails CLOSED (nonzero,
        # empty out), so `completed` is False and any saved verdict is preserved.
        code, out, err = rl.run_codex_streaming_audit(
            sid, cwd, "workspace-write", prompt, timeout=rl.commit_review_timeout(),
            first_output_timeout=rl.first_output_timeout(),
            idle_timeout=rl.idle_timeout(),
            on_cancel=_record_cancellation,
            scratch_dir=str(scratch_dir) if scratch_dir else None,
        )

    # A review COUNTS only if it exited cleanly AND returned something. Exit 0 with empty output
    # is not a verdict: a timeout or a crash returns empty or partial output, and so does a wedged
    # session. Both persistent effects below hang off this one condition.
    completed = code == 0 and bool((out or "").strip())

    approved = code == 0 and rl.is_approved(out)
    review_path = str(rl.last_review_file(cwd, claude_sid))
    # Empty when no reviewer session was established: every per-session accessor treats a falsy
    # id as "no state", so nothing is read or written and no continuity is implied.
    audit_sid = sid if session_known else ""

    # One record per ATTEMPT, and it must be built BEFORE the two persistent writes below.
    # The counters need the FULL stderr, of which the log line keeps 300 characters -- but the
    # ordering matters for a second reason: the capsule NAMES the saved verdict only when one
    # already exists, so a record built after save_review would state a capsule the reviewer
    # never saw. Returns None rather than raising, so instrumentation can never be the reason a
    # fail-closed gate fires.
    audit = ra.record_commit_attempt(
        proj=cwd, claude_session=claude_sid, reviewer_session=audit_sid, backend=backend,
        prompt=prompt, code=code, out=out, err=err, completed=completed, approved=approved,
        verdict_file=review_path if completed else None, change_block=change_block,
        ignore_matcher=ignore_matcher,
    ) or {}

    # The capsule NAMES this file to the reviewer as its own prior findings, so writing an empty
    # result would destroy exactly what the retry exists to reuse, in the same breath as reporting
    # "no verdict". Nothing is lost by not writing -- the log line below carries verdict_tail and
    # stderr_tail -- and review_path is only ever REPORTED on the completed-but-rejected path.
    if completed:
        rl.save_review(out, proj=cwd, claude_session_id=claude_sid, history=True)
        # Remember what this reviewer session has now seen, so the NEXT attempt can be told which
        # paths moved. Only on a completed audit: a review that returned nothing read nothing
        # conclusive, and recording it would make the retry receive an UNCHANGED row telling it to
        # "restate the findings you raised" when there are none and no saved verdict to restate.
        # And only when a session actually exists to remember it -- see audit_sid above.
        if session_known:
            rl.record_audited_stage(cwd, claude_sid, sid)
        # Consume the rotation carryover marker ONLY now, and only when this review actually
        # DELIVERED it (carry_block is non-empty). A review that did not complete, or one whose
        # delivery silently failed (a transient verdict read -> empty carry_block), leaves the marker
        # armed so the retry re-delivers -- a delivery is never lost before it is reviewed. Done
        # BEFORE the rotation check below, which may arm a fresh marker for the NEXT successor.
        if carry_block:
            rl.consume_commit_carryover(cwd, claude_sid, carry_gen)
    else:
        # A timeout, an operational failure and an empty response are all real events in this
        # project's review history, and used to appear only in the log line. They are recorded
        # as OUTCOMES rather than verdicts, so the saved latest ruling -- which the capsule
        # shows the next reviewer as its own prior findings -- is left exactly as it was, and
        # nothing is recorded as audited.
        if code == rl.TIMEOUT_EXIT:
            outcome, detail = rl.OUTCOME_TIMEOUT, (
                "the review exceeded the {}s budget and produced no verdict".format(
                    rl.commit_review_timeout())
            )
        elif code == rl.SCHEMA_VERDICT_MISSING:
            # codex exited 0 but its --json verdict was missing or unparseable: the reviewer ran
            # yet produced no usable ruling, which is the EMPTY case, not an operational FAILURE.
            outcome, detail = rl.OUTCOME_EMPTY, "exit 0 but no schema-conforming verdict"
        elif code != 0:
            outcome, detail = rl.OUTCOME_FAILED, "exit {}: {}".format(
                code, rl.short(err, 2000))
            if rl.usage_limit_detail(err):
                detail += "\n\n" + rl.usage_limit_message(backend, err)
            elif rl.model_rejection_detail(backend, err):
                detail += "\n\n" + rl.model_rejection_message(backend, err)
        else:
            outcome, detail = rl.OUTCOME_EMPTY, "exit 0 with no output"
        rl.record_review_outcome(
            cwd, claude_session_id=claude_sid, outcome=outcome, detail=detail,
            backend=backend,
        )
    provenance = rl.context_provenance(cwd, transcript_path, claude_sid)
    rl.log(
        "[commit] key={} cwd={} backend={} sid={} exit={} approved={} effort={} fixes={} "
        "dur_s={} t_first={} max_gap={} idle_retried={} tokens={} "
        "staged_files={} staged_lines={} ignore_matched_files={} ignore_matched_lines={} "
        "delta_diffs={} full_reads={} test_runs={} "
        "test_runs_failed={} exec_failed={} cmd={!r} "
        "verdict_tail={!r} stderr_tail={!r}{}".format(
            key, cwd, backend, sid, code, approved,
            _effort_label(backend), audit.get("required_fixes"),
            audit.get("dur_s"), audit.get("t_first_event_s"), audit.get("max_gap_s"),
            audit.get("idle_retried"), audit.get("tokens_cumulative"),
            audit.get("staged_files"), audit.get("staged_lines"),
            audit.get("ignore_matched_files"), audit.get("ignore_matched_lines"),
            audit.get("exec_delta_diffs"), audit.get("exec_full_reads"),
            audit.get("exec_test_runs"), audit.get("exec_test_runs_failed"),
            audit.get("exec_failed"), rl.short(command, 120),
            rl.tail(out, 800), rl.tail(err, 300),
            ("\n" + provenance) if provenance else "",
        )
    )

    automatically_rotated = False
    if completed and session_known and not retry_audit:
        _, automatically_rotated = rl.record_completed_reviewer_audit(
            cwd, claude_sid, sid, preserve_last_review=not approved
        )
    if completed and approved:
        rl.clear_commit_retry_pending(cwd, claude_sid)
    elif completed:
        rl.set_commit_retry_pending(cwd, claude_sid)

    if code == rl.TIMEOUT_EXIT:
        # A timeout is NOT a verdict, so it still blocks (fail-closed). But it is a distinct
        # failure with a distinct remedy, and reporting it as a generic "review failed" left
        # the operator guessing whether to retry or to raise the budget.
        secs = rl.commit_review_timeout()
        reason = (
            "reviewerAgent: the commit review TIMED OUT after {}s, so it produced no "
            "verdict. The commit is blocked (fail-closed) -- a timeout is not an approval. "
            "Retry the identical commit: the reviewer session is resumed BY ID, so it keeps "
            "what it already read and a second attempt often lands. If this diff is simply "
            "too large to audit in {}s, raise the budget with `/reviewerModel timeout {}` "
            "and retry.".format(secs, secs, max(secs * 2, secs + 600))
        )
        msg = (
            "[reviewerAgent] TIMEOUT: the commit review exceeded {}s. Commit blocked "
            "(fail-closed; a timeout carries no verdict). Retry, or raise it with "
            "/reviewerModel timeout <seconds>.".format(secs)
        )
        print(json.dumps(rl.deny_output(reason, msg)))
    elif code != 0:
        # A refused model or effort is a CONFIGURATION failure with a specific remedy, and
        # reporting it as a generic "review failed (exit 1)" left an operator staring at a
        # blocked project with the provider's own sentence truncated into a stderr tail.
        # A usage limit is checked FIRST and separately: its remedy is the opposite of a model
        # rejection's -- wait or switch backend, rather than change the selection -- and
        # `rate limit` deliberately does not match the rejection patterns, so without this the
        # exhausted-quota case fell through to a bare "review failed (exit 1)".
        if rl.usage_limit_detail(err):
            diagnosis = rl.usage_limit_message(backend, err)
            reason = (
                "reviewerAgent is ON but the commit review could not run, so the commit was "
                "blocked (fail-closed).\n\n{}".format(diagnosis)
            )
            msg = "[reviewerAgent] ERROR: the reviewer hit a usage limit. Commit "\
                  "blocked.\n{}".format(diagnosis)
        elif rl.model_rejection_detail(backend, err):
            diagnosis = rl.model_rejection_message(backend, err)
            reason = (
                "reviewerAgent is ON but the commit review could not run, so the commit was "
                "blocked (fail-closed).\n\n{}".format(diagnosis)
            )
            msg = "[reviewerAgent] ERROR: the reviewer refused the configured model or "\
                  "effort. Commit blocked.\n{}".format(diagnosis)
        else:
            reason = (
                "reviewerAgent is ON but the commit review FAILED (exit {}): {}. "
                "The commit was blocked (fail-closed). Fix the reviewer or run "
                "/reviewerAgent off.".format(code, err.strip())
            )
            msg = (
                "[reviewerAgent] ERROR: commit review failed (exit {}). Commit "
                "blocked.\n{}".format(code, rl.short(err, 300))
            )
        print(json.dumps(rl.deny_output(reason, msg)))
    elif not completed:
        # Exit 0 but nothing came back. Still fail-CLOSED, but it must not fall through to the
        # rejection branch: that would report review_path as holding "the complete list" when
        # nothing was written this round, so the agent would read a PREVIOUS attempt's findings
        # and treat them as current.
        reason = (
            "reviewerAgent: the commit review returned an EMPTY verdict (exit 0, no output), "
            "so there is nothing to act on. The commit is blocked (fail-closed) -- an empty "
            "response is not an approval. Retry the identical commit; if it keeps coming back "
            "empty, the reviewer session is wedged, so run /reviewerSession rotate and retry."
        )
        msg = (
            "[reviewerAgent] ERROR: the commit review returned no verdict (exit 0, empty "
            "output). Commit blocked. Retry, or /reviewerSession rotate."
        )
        print(json.dumps(rl.deny_output(reason, msg)))
    elif approved and commit_request is not None and state.get("txn") is not None:
        # The gate performs the reviewed commit ITSELF and DENIES the tool call (claude path).
        # The candidate is built against the captured snapshot -- the repository's real hooks run
        # -- and is rejected if a hook moved the tree or parents; publication then moves the ref
        # under the index and reference locks, re-verifying the live state, so nothing but the
        # reviewed tree can land. The denial NAMES the published commit id first, so an agent that
        # reads "denied" does not commit a second time.
        transaction = state["txn"]
        suffix = "\n--- the reviewer said ---\n{}".format(rl.short(out, 1500))
        if not automatically_rotated:
            suffix += "\n(full review: {})".format(review_path)
        try:
            candidate_sha, _ = transaction.candidate(
                commit_request["message"], commit_request["options"]
            )
            published = transaction.publish(candidate_sha, commit_request["message"])
        except rct.CommitPublished as exc:
            # The commit IS public; only a step after it failed. Never report "nothing committed",
            # which is exactly what would prompt an agent to commit a duplicate.
            rl.log("[commit] key={} published_with_trailing_error commit={} err={!r}".format(
                key, exc.commit, rl.short(str(exc), 200)))
            print(json.dumps(rl.deny_output(
                "reviewerAgent APPROVED and the commit was PUBLISHED as {}. Do NOT commit again "
                "-- a retry would duplicate it. A step after publication failed: {}".format(
                    exc.commit, exc),
                "[reviewerAgent] APPROVED and committed as {} (do not re-commit).".format(
                    exc.commit),
            )))
        except rct.CommitCancelled as exc:
            rl.log("[commit] key={} commit cancelled before publication: {!r}".format(
                key, rl.short(str(exc), 200)))
            print(json.dumps(rl.deny_output(
                "reviewerAgent APPROVED, but the commit was CANCELLED before publication, so "
                "nothing was committed and your staged changes are intact. Retry the commit.",
                "[reviewerAgent] commit cancelled before publication; nothing committed.",
            )))
        except rct.CommitError as exc:
            rl.log("[commit] key={} commit blocked after approval: {!r}".format(
                key, rl.short(str(exc), 300)))
            print(json.dumps(rl.deny_output(
                "reviewerAgent APPROVED the review, but the commit was not published: {}. "
                "Nothing was committed and your staged and unstaged changes are intact. Retry "
                "the commit.".format(exc),
                "[reviewerAgent] commit blocked after approval: {}".format(
                    rl.short(str(exc), 200)),
            )))
        else:
            rl.log("[commit] key={} published reviewed commit={}".format(key, published))
            note = ""
            if automatically_rotated:
                note = (" reviewer session auto-rotated after the configured number of completed "
                        "non-retry audits; the next audit starts fresh.")
            print(json.dumps(rl.deny_output(
                "reviewerAgent APPROVED and PUBLISHED the reviewed commit as {}. Do NOT run the "
                "commit again -- the gate performed it, so a retry would duplicate it or find "
                "nothing staged. This tool call is declined only because the commit is already "
                "done.{}".format(published, suffix),
                "[reviewerAgent] APPROVED -> committed as {} (do not re-commit).{}".format(
                    published, note),
            )))
    elif approved:
        # Non-claude backends: the reviewer approved and the agent's own `git commit` proceeds.
        # An APPROVED verdict may now carry a SUGGESTIONS block, so the old short(out) at 600
        # characters would cut the list mid-item -- and with no ellipsis the operator could
        # not tell. Bounded at 1500 with the on-disk path named, which is exactly what
        # required_fixes_message() does for the operator-facing half of a rejection.
        #
        # NOT unbounded, even though suggestions are the point of the change: `systemMessage`
        # is the ONLY channel on the allow path (there is no permissionDecisionReason here),
        # so an off-contract reviewer emitting an essay would dump all of it on every approved
        # commit. save_review() has already written the complete text for any completed audit,
        # so naming the file loses nothing.
        msg = (
            "[reviewerAgent] git commit -> the reviewer APPROVED the staged changes. Letting "
            "the commit proceed. Commit ONLY the already-staged set -- do not stage "
            "more files or amend the diff after this approval.\n"
            "--- the reviewer said ---\n{}".format(rl.short(out, 1500))
        )
        if not automatically_rotated:
            msg += "\n(full review: {})".format(review_path)
        if automatically_rotated:
            msg += (
                "\n[reviewerAgent] reviewer session automatically rotated after the "
                "configured number of completed non-retry audits; the next audit starts fresh."
            )
        print(json.dumps(rl.allow_output(msg)))
    else:
        reason, msg = rl.required_fixes_message(out, review_path)
        print(json.dumps(rl.deny_output(reason, msg)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
