# Injection-safe reviewer-agent commands: hook for the free-text one, dispatcher for the rest

## Operator TDD-commit policy (governs this work)

> **plan → commit → red tests → commit → code that passes all tests (green) → commit**

An intentionally-failing ("red") test committed as the red step of this policy is expected; the green
commit that makes it pass is reviewed as strictly as ever.

## The problem

The 7 operator-control skills each run a bang pre-exec that invokes a toggle script with
`$ARGUMENTS`. Two defects:

1. **Permission friction.** A bang pre-exec is not covered by a skill's `allowed-tools` frontmatter
   (that governs only model tool calls); it is checked against the session's permission rules at
   expansion time, and in `auto` mode a miss hard-fails instead of prompting. The 7 skills name 7
   different scripts, so a scoped (non-blanket) allow-list needs up to 7 rules, and a plugin cannot
   ship permission rules for itself.
2. **Shell injection / breakage.** `$ARGUMENTS` is text-substituted into the command string BEFORE
   bash parses it. So a parenthesis is a syntax error, an arrow becomes a redirect, and a command
   substitution, backticks, or an embedded quote execute. **Quoting does not fix this** — verified
   against the Claude Code docs, there is no environment-variable channel and every argument form is
   text substitution. The only genuinely injection-proof channel for a slash command to receive free
   text is a hook reading the prompt as JSON on stdin.

Only `reviewerDirective` takes genuinely free-form text (a policy sentence). The other six take
constrained tokens — `on`/`off`/`acceptRisk`, an interval integer, a model id, a session id — which
never legitimately contain shell metacharacters.

## Decision: split by input shape

### A. reviewerDirective (free text) → UserPromptSubmit hook (injection-proof)

- `skills/reviewerDirective/SKILL.md` drops the bang. Its body becomes a distinctive sentinel line
  followed by the raw arguments as PLAIN prompt text (no shell). On `/reviewerDirective ...` the
  skill expands to that sentinel line and it becomes the submitted prompt.
- The plugin's existing `userpromptsubmit_hook.py` gains a branch BEFORE the watchdog re-arm: if the
  prompt begins with the sentinel, read the directive text from the JSON `prompt` field (NEVER
  shell-parsed → injection-proof), record it, and return a blocking result. Non-sentinel prompts
  fall through to the existing watchdog re-arm untouched.
- **Project resolution (was a bug):** the hook already resolves `cwd = data.get("cwd") or
  rl.project_dir()` (userpromptsubmit_hook.py:48) — the AUTHORITATIVE project from the payload, not
  the hook process's own cwd. The directive branch passes THAT `cwd` into the directive logic, so a
  directive records under the right repository even though the hook process cwd may differ.
- **Blocking output shape (repo contract):** suppress the model turn with the repository's blocking
  shape — TOP-LEVEL `decision: "block"`, `reason`, `systemMessage` (mirrors
  `stop_block_output`, reviewer_prompts.py:1677-1684) — NOT a nested `hookSpecificOutput.decision`.
  `reason`/`systemMessage` carry the outcome text shown to the operator.
- **Logic reuse without stdout capture:** `directive_toggle` grows `run(args, proj) -> (exit_code,
  message)` doing the work with an explicit project and returning (not printing) the message.
  `main(argv)` becomes `code, msg = run(argv[1:], os.getcwd()); print(...)` (CLI unchanged); the hook
  calls `run(tokens, cwd)` and puts `message` in `reason`.
- Verified: UserPromptSubmit fires with the fully-expanded skill body; hooks run as trusted infra
  (no permission rule, works in `auto` mode); a block decision suppresses the model turn. Accepted,
  documented caveats: the pattern is not a first-class API and is version-fragile, and the outcome
  renders as a block message rather than a normal echo — the price of genuine injection-safety for
  the one free-text command.

### B. The other six → one dispatcher (one scoped permission rule)

- New `reviewer_cmd.py` at the plugin root: a subcommand table mapping
  `agent/history/model/session/stopcontinue/watchdog` to the matching existing toggle module, and
  delegating to that module's existing `main()`. The six toggles keep all their logic AND their own
  argument validation (e.g. the model validator permits `/` for namespaced ids,
  reviewer_model_toggle.py:414-417) — the dispatcher does NOT re-validate, so it cannot diverge from
  a toggle's contract.
- Each of the six skill bangs is rewritten to call the dispatcher with the subcommand and a QUOTED
  `"$ARGUMENTS"` (so an accidental space/metacharacter in a mistyped constrained arg does not break
  bash). For these six the arg is a constrained token, so the residual command-substitution exposure
  has no realistic surface (the operator types `on`/`off`/an id, not pasted untrusted text). The
  fully injection-proof channel (the hook) is reserved for the free-text command in A.
- The dispatcher reconstructs the toggle's token list with `" ".join(argv[2:]).split()` — quoting
  makes multi-token args like `on acceptRisk` arrive as one string; plain whitespace split restores
  them and does not choke on apostrophes — then calls `mod.main([subcmd] + tokens)`. Unknown/missing
  subcommand → stderr error + exit 2.
- Operators add exactly one scoped rule (paths are literal — no `${CLAUDE_PLUGIN_ROOT}` expansion):
  a `Bash(python3 "<resolved plugin path>/reviewer_cmd.py":*)` allow entry.

## Files

New: `reviewer_cmd.py`; `tests/test_reviewer_cmd_dispatcher.py`; hook + directive tests.
Changed: the six `skills/*/SKILL.md` bang lines; `skills/reviewerDirective/SKILL.md` (sentinel, no
bang); `userpromptsubmit_hook.py` (sentinel branch, reusing its existing `cwd` resolution);
`directive_toggle.py` (`run(args, proj)` split; the stale "unquoted" comment); `AGENT_INSTALL.md`
(one scoped rule; reviewerDirective now needs none).

**Published warnings must be corrected so they stay true** — README.md:651-653, SECURITY.md:43-45,
and DISCLAIMER.md:120-122 all currently say the manual commands use `!` shell preprocessing and tell
users to avoid shell syntax and not paste untrusted text into `/reviewerDirective`. The correction
splits by concern, and must NOT overclaim safety:

- `/reviewerDirective` becomes **shell-safe** — the hook reads the text from the JSON prompt, never a
  shell, so shell syntax (`(green)`, `->`, quotes) no longer breaks it and there is no shell
  execution from `$(...)` or backticks. The distinct **prompt-injection** caveat REMAINS: directive
  text is persisted and injected into every reviewer prompt (SECURITY.md already frames a crafted
  directive as a prompt-injection vector against the reviewer), so pasting untrusted content is still
  risky. So for `/reviewerDirective` drop only the *shell-syntax* warning; KEEP the
  untrusted-content warning, reframed as prompt-injection rather than shell processing.
- The six `!`-preprocessed dispatcher commands keep the shell-syntax caveat: their raw argument is
  still interpolated before Bash parses it. Because their args are constrained tokens
  (`on`/`off`/`acceptRisk`/an id/an integer) the exposure is documented and operator-accepted rather
  than eliminated — the fully injection-proof channel is reserved for the free-text command. (This
  is the non-blocking suggestion made explicit; the operator chose this split deliberately.)

Unchanged: the six `*_toggle.py` keep their logic and stay directly invokable; existing toggle tests
stay valid.

## Tests (red first)

1. Dispatcher routing: each subcommand delegates to its toggle's `main` with the right argv.
2. Token preservation: dispatching `stopcontinue` with the single arg `on acceptRisk` delegates
   `["stopcontinue_toggle", "on", "acceptRisk"]`.
3. Unknown subcommand: exit 2, no delegation.
4. Directive `run(args, proj)` honors the passed project, NOT `os.getcwd()`: call it with `proj` set
   to an isolated repo different from the process cwd and assert the directive lands under `proj`.
5. Directive hook, injection-proof: feed the UserPromptSubmit hook a prompt of the sentinel plus
   `$(touch SENTINEL) and a "quote"`, where `SENTINEL` is a path under the test's own `tmp_path`
   asserted absent beforehand; assert afterward that (a) it was NOT created (nothing executed),
   (b) the directive is recorded with that verbatim text under the payload `cwd`, and (c) the hook
   output blocks the prompt (top-level `decision: "block"`).
6. Directive hook, non-sentinel prompts fall through to the existing watchdog re-arm untouched.
7. Skill wiring: the six bangs call the dispatcher with a subcommand and quote `"$ARGUMENTS"`;
   `reviewerDirective` has no bang and carries the sentinel plus `$ARGUMENTS`.

## Held for operator sign-off

Version bump + dist regen + push (the outward release steps). The watchdog-tick per-path fix stays
parked (separate task/files).
