# Plan: Reroute AskUserQuestion through stopContinue (retire the afkTimeoutMs answerer)

## Problem

The AskUserQuestion timeout-answering feature (`askq_answer_hook.py`, a `PostToolUse` hook)
depended on a Claude Code behavior: an unanswered `AskUserQuestion` auto-closes after
`askUserQuestionTimeout` and returns a `tool_response` carrying `afkTimeoutMs` with an empty
`answers` map. Only then did the hook ask the reviewer to answer on the operator's behalf.

That auto-close **stopped firing** in the operator's Claude Code (2.1.257). Forensics from
`~/.claude/reviewer-agent/logs/reviewer.log`:

- 88 captured questions; `afkTimeoutMs` present on 42 of them, the **last on 2026-08-21**. None
  since.
- The current session's questions all show a human answer (non-empty `answers`, no
  `afkTimeoutMs`) despite waiting **up to 10.9 hours** (transcript timing) — i.e. the question
  blocked indefinitely and the human eventually answered.

Consequence: an unanswered question now blocks forever instead of auto-closing, so `PostToolUse`
fires only once the human answers (with a non-empty `answers`), and `_should_reviewer_answer()`
is never true. The reviewer is never invited. The feature is dead.

Root cause is architectural, not a logic bug: a `PostToolUse` hook cannot act on a tool that
never completes, and **no hook fires while an `AskUserQuestion` is pending** (confirmed) — only
`PreToolUse` (before) and `PostToolUse` (after).

## Approach (empirically verified 2026-09-01)

Intercept `AskUserQuestion` at `PreToolUse` and **deny** it (only when stopContinue is active),
with a deny reason that hands the agent the exact question and tells it to re-ask as plain prose
and stop. The already-working stopContinue Stop-hook supervisor then either answers the question
from established operator context or defers a genuinely-open one to the operator.

This reuses the mechanism stopContinue was built for: `stopcontinue_toggle.py` describes its
target case as "the agent ends its turn with a prose question **instead of calling
AskUserQuestion**." AskUserQuestion is precisely what bypasses stopContinue today; rerouting it
back to prose re-subjects it to the supervisor.

Verification (isolated `claude --settings` session, model Sonnet 5): a `PreToolUse` deny on
`AskUserQuestion` **suppressed the dialog** (no widget rendered), the agent received the deny
reason as the tool result, and it re-asked in plain prose and stopped. The hook received the full
`tool_input` (questions + options), so the deny reason can carry the serialized question verbatim.
The intercept is at the hook layer, so it is model-independent.

## Design

New hook `askq_reroute_hook.py`, registered as `PreToolUse` / matcher `AskUserQuestion`:

- If **not** `rl.is_stopcontinue_active(cwd)`: emit nothing — the tool proceeds and the widget
  renders normally (vanilla Claude Code).
- Else: return `rl.deny_output(reason, system_message)`, where `reason` is
  `rl.serialize_questions(tool_input)` followed by an instruction to ask the question as the
  agent's final plain-prose message (restating the options and a recommendation) and then stop
  and wait.
- **Fail-open**: any exception emits nothing, so a hook error can never wedge a question.

Gate rationale: the answer is produced by the stopContinue supervisor, which works independent of
reviewerAgent. When stopContinue is off there is no supervisor, so denying would strip the widget
with nothing to answer the prose — strictly worse than the widget. Hence the reroute is a
stopContinue capability, not a reviewerAgent one. (Question-answering therefore rides the
stopContinue flag, not the reviewerAgent flag.)

Retire the old path: delete `askq_answer_hook.py`, its `PostToolUse` registration, its tests, and
its manifest entry. Correct the docs that still claim the reviewer "answers timed-out
AskUserQuestion prompts."

### Flag matrix

| stopContinue | AskUserQuestion behavior |
| --- | --- |
| ON | denied at PreToolUse -> agent re-asks as prose and stops -> supervisor answers (established) or defers (genuine) |
| OFF | renders normally; no hook involvement |

### If afkTimeoutMs auto-close returns

The reroute is independent of `afkTimeoutMs`. If the behavior returns: stopContinue-on sessions
are unaffected (the tool is denied before it runs, so `afkTimeoutMs` is never generated);
stopContinue-off sessions simply regain vanilla auto-close. The one capability being dropped is
reviewer-answered AFK questions for reviewerAgent-on / stopContinue-off users; if that is wanted
again, restore `askq_answer_hook.py` from git history. A breadcrumb recording this decision goes
in CHANGELOG.md so a future maintainer does not have to re-derive it forensically.

## Files

- NEW `askq_reroute_hook.py`
- NEW `tests/test_askq_reroute_hook.py`
- `hooks/hooks.json`: drop `PostToolUse`/`AskUserQuestion` -> `askq_answer_hook.py`; add
  `PreToolUse`/`AskUserQuestion` -> `askq_reroute_hook.py`
- DELETE `askq_answer_hook.py`, `tests/test_askq_answer_hook.py`
- Update the tests that reference the old hook: `tests/test_repository_distribution.py`,
  `tests/test_directives.py`, `tests/test_toggle.py`
- `reviewer_manifest.py`: update if it enumerates hook files (verify)
- Docs: `README.md`, `reviewerAgent.md`, `AGENT_UNINSTALL.md`, `SECURITY.md`,
  `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` — replace the
  "answers timed-out AskUserQuestion prompts" language with the stopContinue reroute; `CHANGELOG.md`
  entry + afkTimeoutMs breadcrumb
- Version bump + dist regen per AGENT_UPDATE (a directory-source install is compared by version
  string, so an unchanged version is a no-op update)

## TDD sequence (operator policy: plan -> commit -> red -> commit -> green -> commit)

1. This plan -> commit.
2. Red: `tests/test_askq_reroute_hook.py` (deny shape via `deny_output` when stopContinue on;
   no-deny when off; reason embeds `serialize_questions` output; fail-open on malformed input)
   plus the updated distribution/manifest tests -> commit red.
3. Green: `askq_reroute_hook.py` + `hooks.json` wiring + delete old hook + manifest + docs ->
   commit green.
4. Version bump + dist regen -> commit. Release/push is the operator's call.
