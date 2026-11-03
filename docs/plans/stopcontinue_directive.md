# `/stopContinueDirective`: standing rules for the stop supervisor

## Problem

On 2026-11-03 the stop supervisor let an agent stop after it wrote "Next on the approved list is
the survey box rewording, then the home page sign-up/pricing link, the button order and the Stripe
trials." The operator had approved that list 70 minutes earlier: the agent listed the items as
"all approved, none needing input" and the operator replied "ok". Later the operator said "let's
move on to non-genomic tasks". The supervisor's whole reasoning was "Ruff correction is complete".
It judged the stop against the newest request ("fix them in this commit please") alone.

The supervisor never saw the list the "ok" approved. That agent message came before the previous
approved stop, so it was outside the AGENT'S OWN EARLIER REPLIES window. Replaying the captured
prompt three times gave STOP_OK twice. With that one agent message added beside the operator's
"ok", all three replays directed the agent to continue with the survey box rewording.

The operator has no way to tell the supervisor a standing rule about stopping, such as "items in
HANDOVER.md's approved list are approved; do not stop between them" or "only stop when every task
in the current plan is committed". `/reviewerDirective` is the nearest tool, but it reaches every
reviewer prompt: the commit audit and the question review read it too. A rule about when to stop
has no business in a commit audit.

## Design

A second directive list, read only by the stop supervisor.

- **Command:** `/reviewer-agent:stopContinueDirective [<text> | list | remove <n> | clear]`. It has
  the same subcommands and output as `/reviewerDirective`, and every message names its own command.
- **Store:** `<project stem>.stop_directives.json` in the state directory. It is keyed by the
  canonical project root like the reviewer directives, so a worktree shares its main checkout's
  rules. The format matches `.directives.json`.
- **Injection:** `apply_context` appends a STOP DIRECTIVES block after the operator directives
  when `role == ROLE_STOP`. It never reaches a commit audit or a question review. Reviewer directives
  keep reaching the stop supervisor as they do today.
- **Header:** operator-authored and authoritative on when the agent may stop or must keep working,
  and on authorizations it grants. The same provenance note as the reviewer directives applies: it
  was recorded through a command line, so it is the softer source if it conflicts with the
  operator's typed messages. It does not otherwise change the supervisor's rules.
- **Handling:** the `UserPromptSubmit` hook, like `/reviewerDirective`. It reads the typed command or
  the skill's sentinel `<<reviewer-agent stop directive>>` from the JSON prompt, so no shell sees
  the text. It records the text verbatim and blocks the model turn, and the reason leads with
  `stopContinueDirective SUCCEEDED` or `FAILED:`.
- **Diagnostics:** the `ctx:` log line gains `stop_directives=N`.
- **Code shape:** the directive store functions and `directive_toggle.run` take a `kind` argument
  (`reviewer` by default, or `stop`) instead of being copied.

The Codex plugin has no operator-directive command yet, so this change covers the Claude Code
plugin only.

## Not in this change

The underlying cause, an operator reply shown without the agent message it answered, needs its own
fix: show, for each operator message, the agent message just before it. That changes what every
stop prompt carries and waits for the operator's choice. A stop directive is the operator's lever
in the meantime, and it stays useful after that fix.

## Tests (red first)

`tests/test_stop_directives.py`:

1. An added stop directive reaches the stop supervisor prompt, through `stop_hook.py` with the fake
   codex backend.
2. It does not reach a commit audit prompt (`commit_hook.py`), nor `apply_context` without
   `ROLE_STOP`.
3. Reviewer directives and stop directives are separate lists: adding, removing or clearing one
   leaves the other untouched.
4. The block names the command, says the directives are operator-authored and authoritative about
   stopping, and keeps the "does not otherwise" limit.
5. A worktree shares its main checkout's stop directives.
6. The UPS hook records `/reviewer-agent:stopContinueDirective <text>`, `/stopContinueDirective`
   and the sentinel form verbatim, with shell syntax never executed. It blocks with a
   `stopContinueDirective SUCCEEDED` reason, `list` shows the rule, and `remove 99` reports
   `stopContinueDirective FAILED:`.
7. `/stopContinueDirectives` (a longer name) is not handled, and a `/reviewerDirective` invocation
   records nothing in the stop list.
8. The skill carries the sentinel and `$ARGUMENTS`, has no bang line, and is not model-invocable.
   The distribution test accepts it as a second hook-handled skill.

## Docs

The README gets a command-table row and adds the command to the hook-handling note. AGENT_INSTALL
says it needs no permission rule. SECURITY and DISCLAIMER put it next to `/reviewerDirective`, and
CHANGELOG gets an entry.
