# Codex JSONL Streaming Audit — strict-schema verdict + dual-threshold hang detection

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> subagent-driven-development) to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax. Each task ends with a commit through the review gate.

**Goal.** Make the codex commit-audit path (a) return a **strict-JSON, schema-constrained verdict**
so parsing is deterministic, and (b) detect a hung reviewer via a **JSONL event stream** with two
separate idle thresholds, killing and retrying while budget and the `MAX_IDLE_RETRIES` cap allow
before the fail-closed total-cap. Active by default.
Only the codex **commit-audit** path changes; pi, claude, stop, and askq paths are untouched.

**Tech stack.** Python stdlib only (select, subprocess, json, os, signal). No new dependencies.

---

## What we verified (codex-cli 0.149.1, model gpt-5.6-luna, live probes)

1. **`codex exec [resume] --output-schema FILE -o OUT` honors the schema even when the turn uses
   tools.** A read-only probe made the model run `ls` then answer under an **ad-hoc minimal schema
   `{verdict, note}` used ONLY to prove the mechanism**; it produced a clean, schema-conforming
   `-o` file (`{"verdict":"APPROVED","note":"3"}`). The production verdict schema is different —
   `{verdict, findings, suggestions}` (Task 1). GitHub issue #15451 (schema dropped with tools,
   reported on 0.116.0) does **not** reproduce here. `resume` accepts `--output-schema`, `--json`,
   and `-o` (confirmed in `codex exec resume --help`).
2. **`--json` emits events only at ITEM BOUNDARIES — no token/reasoning deltas.** Observed stream:
   `thread.started` (~1s) → `turn.started` → `item.completed:agent_message` (after a silent
   reasoning gap) → `item.started:command_execution` → **silence for the whole command** →
   `item.completed:command_execution` → `item.completed:agent_message` → `turn.completed` (carries
   `usage`). A 12s command produced a **13.7s** gap with zero output.
3. **Therefore a single idle threshold cannot both catch startup hangs fast and tolerate a long
   test run.** The reviewer's own suite runs ~328s and would be one silent `command_execution`
   gap. So we use **two thresholds** (below). The schema requires `additionalProperties: false`.

Schema requirement: OpenAI structured output needs `additionalProperties: false` on every object
and all properties listed in `required`.

## Design

- **Verdict via strict schema.** Ship a static JSON Schema file `codex_verdict_schema.json` beside
  `reviewer_backends.py`. Pass `--output-schema <that file> -o <temp>` on the codex resume audit.
  codex writes the schema-conforming final message to the temp file. Parse it and **reconstruct the
  canonical text verdict** so `commit_hook.is_approved(out)` and `reviewer_audit` stay
  backend-agnostic and unchanged. The verdict contract has THREE parts (verified against the codex
  commit prompt, `reviewer_prompts.py`): `verdict` (`APPROVED`/`REJECTED`), `findings` (the
  numbered REQUIRED corrections, non-empty only when REJECTED), and `suggestions` (the non-blocking
  `SUGGESTIONS:` block, allowed on BOTH APPROVED and REJECTED). Dropping suggestions would silently
  lose approved-with-suggestions notes and could turn a suggestion into a required fix — so the
  schema and reconstruction MUST carry all three. Schema:
  `{verdict:"APPROVED"|"REJECTED", findings:[string,…], suggestions:[string,…]}`,
  `additionalProperties:false`, all three required. Reconstruction: APPROVED → `"APPROVED"` + (if
  suggestions) a `"\nSUGGESTIONS:\n1. …"` block; REJECTED → `"REJECTED:\n1. …"` from findings + (if
  suggestions) the same SUGGESTIONS block — byte-matching what `is_approved` and the developer
  already expect. **Fail closed** on a missing/malformed `-o` file: return a NONZERO code with an
  empty verdict (never a nonzero-length string at exit 0 — that would satisfy `commit_hook`'s
  `completed` test and overwrite the preserved prior verdict; see Task 4).
- **Hang detection via `--json` + two thresholds** in a new `_run_streaming`, checked each poll in
  THIS order — **total cap FIRST**, then first-output, then between-output — so a genuine
  total-timeout is never misclassified as an idle kill:
  - **Total cap.** `_run_streaming` takes an absolute per-call budget; when exceeded it returns
    `TIMEOUT_EXIT` with reason `"review timed out after Ns"` (NO "idle" substring), so it is NOT
    retried. `commit_review_timeout` (default 900s) supplies the budget.
  - **N1 — first-output timeout (default 30s, floor 5s).** No byte on stdout OR stderr within N1 of
    launch ⇒ kill, reason `"idle timeout: no output within Ns"`. Catches "codex launched but the API never
    responded" (the observed empty-output 1200s strand); `thread.started` normally lands ~1s.
  - **N2 — between-output timeout (default 900s, floor 60s).** After the first byte, a gap between
    consecutive bytes longer than N2 ⇒ kill, reason `"idle timeout: no output for Ns"`. Must exceed the longest
    legitimate silent item (a test run); 900s clears the observed ~300-370s healthy gaps on a large
    project with ~2.4x headroom (see `docs/plans/codex_audit_retry_budget.md`).
  - One timer whose allowed gap is N1 before the first byte, N2 after; any byte on either stream
    resets it.
- **EOF / child-exit handling (required for correctness).** `select()` reports a closed pipe as
  readable, so without this a completed review spins to the total cap. A read returning empty bytes
  = EOF ⇒ drop that fd from the watch set; when both fds are EOF OR `process.poll()` is not None,
  DRAIN any remaining readable bytes, then return promptly with the process return code.
- **Retry any N1/N2 idle kill while budget remains AND retries < MAX_IDLE_RETRIES** (default 8);
  never retry the total cap. The whole audit shares **one absolute deadline** = start + total
  budget; each retry runs with the REMAINING budget and `_run_streaming` checks that cap first —
  so total wall time is bounded by the single total timeout, then fail-closed. See
  `docs/plans/codex_audit_retry_budget.md` for the observation-driven rationale that replaced the
  earlier single-retry design.
- **Active by default.** `commit_hook` uses the streaming audit for codex unconditionally — no flag.
  `run_codex_resume_by_id` is left in place for the stop and askq paths.
- **Configurable three ways**, resolved env > state file > config.json > default, clamped to the
  floor (mirrors `commit_review_timeout_detail`):
  - N1: `REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS` > state `first_output_timeout` > config
    `firstOutputTimeoutSecs` > 30.
  - N2: `REVIEWER_IDLE_TIMEOUT_SECS` > state `idle_timeout` > config `idleTimeoutSecs` > 900.
  - Slash commands: `/reviewerModel firstOutputTimeout <secs>|clear` and
    `/reviewerModel idleTimeout <secs>|clear`.
- **Instrumentation** for later calibration: record `t_first_event_s`, `max_gap_s` (largest
  inter-event gap), `idle_retried`, and structured `turn_usage` on `last_invocation()`, surfaced in
  the audit record and the reviewer log line.

## Global constraints

- **Fail-closed:** any error, parse failure, missing `-o` file, or unexpected state BLOCKS the
  commit. Never silently approve.
- `_run` is unchanged — `_run_streaming` is a new parallel function.
- `run_codex_resume_by_id` is unchanged — stop_hook.py and askq_answer_hook.py are unaffected.
- Only the **codex** commit prompt's verdict-format instructions change (to the JSON schema); pi and
  claude keep their text verdicts.
- All new public names re-exported through the `reviewer_lib.py` facade; regenerate
  `tests/test_facade_contract.py` with `tools/gen_facade_contract.py`.
- TDD: write the failing test, then the implementation.
- POSIX `select.select()` on pipes; on non-POSIX, `_run_streaming` falls back to `_run` (no idle
  detection there) so Windows does not break.

---

### Task 1: Verdict schema + strict-JSON→canonical-text parser

**Files:** new `codex_verdict_schema.json`; modify `reviewer_backends.py` (parsers after
`parse_session_id`, ~line 357); modify `reviewer_prompts.py` (codex commit-prompt verdict block);
test `tests/test_audit_record.py`.

**Interfaces produced:**
- `codex_verdict_schema_path() -> str` — absolute path to the shipped schema file.
- `parse_codex_verdict_json(text) -> dict|None` — the parsed `{verdict, findings, suggestions}`, or
  None if absent/malformed. STRICT: `json.loads` a single object, then require exactly the keys
  `verdict`/`findings`/`suggestions`, `verdict` a string in the enum, `findings` and `suggestions`
  lists whose every element is a string. Any deviation (extra/missing key, wrong type, non-string
  element, non-object, JSON error) → None. ALSO enforce the verdict/findings CONSISTENCY the prompt
  contract requires: **APPROVED ⇒ `findings == []`** (an approval has no required fixes) and
  **REJECTED ⇒ `findings` non-empty** (a rejection must list ≥1 required correction). An
  inconsistent combo (APPROVED-with-findings or REJECTED-with-no-findings) → None, so it fails
  closed rather than being silently accepted. Never raises, never partially accepts.
- `codex_verdict_to_text(obj) -> str` — canonical reconstruction (APPROVED/REJECTED + findings +
  SUGGESTIONS block), or `""` if `obj` is None/invalid (drives fail-closed).
- `parse_codex_turn_usage(stdout) -> dict|None` — usage from the last `turn.completed` event.

- [ ] **Step 1: Write the schema file** `codex_verdict_schema.json` (all three keys required;
  `additionalProperties:false` — an OpenAI structured-output requirement). The schema fixes the
  SHAPE; the APPROVED-empty-findings / REJECTED-nonempty-findings CONSISTENCY is enforced in the
  parser (Step 4), not via schema `if/then`, since structured-output support for conditionals is not
  something we rely on:
  ```json
  {
    "type": "object",
    "additionalProperties": false,
    "required": ["verdict", "findings", "suggestions"],
    "properties": {
      "verdict": {"type": "string", "enum": ["APPROVED", "REJECTED"]},
      "findings": {"type": "array", "items": {"type": "string"}},
      "suggestions": {"type": "array", "items": {"type": "string"}}
    }
  }
  ```
- [ ] **Step 2: Failing tests** for `parse_codex_verdict_json` — valid APPROVED (findings=[],
  suggestions maybe non-empty); valid REJECTED with findings and suggestions; **strict rejections
  all → None**: malformed JSON, missing any of the three keys, an EXTRA key, `verdict` outside the
  enum, `findings`/`suggestions` not a list, a non-string element, a non-object top level, **and the
  two inconsistent combos — APPROVED with non-empty `findings`, and REJECTED with empty `findings`.**
  `codex_verdict_to_text` — APPROVED no-suggestions → `"APPROVED"`; APPROVED + suggestions →
  `"APPROVED\nSUGGESTIONS:\n1. …"`; REJECTED + findings (+ suggestions) →
  `"REJECTED:\n1. …\n2. …"` (+ SUGGESTIONS block); None → `""`. `parse_codex_turn_usage` — reads
  last `turn.completed.usage`; None when absent.
- [ ] **Step 3: Run tests — expect FAIL (AttributeError).**
- [ ] **Step 4: Implement** the functions in `reviewer_backends.py`. `parse_codex_verdict_json` does
  one `json.loads` of the whole `-o` file text (a single JSON object, not JSONL) and enforces the
  COMPLETE schema described in Interfaces (exact keys, enum, list-of-strings) — any deviation → None,
  never raising. `codex_verdict_to_text` reconstructs the exact bytes `is_approved`/`reviewer_audit`
  and the developer expect, including the `SUGGESTIONS:` block on both verdicts.
  `parse_codex_turn_usage` scans JSONL for the last `turn.completed` `usage` dict.
- [ ] **Step 5: Update the codex commit prompt** in `reviewer_prompts.py`: replace the "begin the
  first line with APPROVED / REJECTED:" instruction (codex variant only) with "emit ONLY a JSON
  object matching this schema" and inline the annotated schema (a one-line description per field).
  Leave pi/claude prompts unchanged. Update any prompt test that asserts the old codex wording.
- [ ] **Step 6: Run tests — expect PASS.**
- [ ] **Step 7: Facade** — add `codex_verdict_schema_path`, `parse_codex_verdict_json`,
  `codex_verdict_to_text`, `parse_codex_turn_usage` to `reviewer_lib.py`; regenerate the contract.
- [ ] **Step 8: Commit** — `feat: add strict-JSON codex verdict schema, parser, and text reconstruction`

---

### Task 2: `_run_streaming` with dual idle thresholds + timing

**Files:** modify `reviewer_backends.py` (`_run_streaming` after `_run`, ~267; extend
`_record_invocation`); modify `reviewer_state.py` (N1/N2 config, near `commit_review_timeout`);
test `tests/test_process_cancellation.py`.

**Interfaces produced:**
- `_run_streaming(argv, timeout, cwd, on_cancel, first_output_timeout, idle_timeout) -> (int,str,str)`
- `first_output_timeout()` / `first_output_timeout_detail()`, `idle_timeout()` /
  `idle_timeout_detail()`, plus `*_file` / `set_*` / `clear_*` for each.
- `_record_invocation` gains `t_first_event_s`, `max_gap_s`.

- [ ] **Step 1: Config in `reviewer_state.py`** — two resolvers mirroring
  `commit_review_timeout_detail` exactly (env > state file > config.json > default, clamped):
  `DEFAULT_FIRST_OUTPUT_TIMEOUT=30`, `FIRST_OUTPUT_TIMEOUT_FLOOR=5`, keys
  `REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS` / state `first_output_timeout` / `firstOutputTimeoutSecs`;
  `DEFAULT_IDLE_TIMEOUT=900`, `IDLE_TIMEOUT_FLOOR=60`, keys `REVIEWER_IDLE_TIMEOUT_SECS` / state
  `idle_timeout` / `idleTimeoutSecs`. Plus `set_*`/`clear_*`/`*_file` for both.
- [ ] **Step 2: Failing tests** in `tests/test_process_cancellation.py`: returns stdout+stderr on a
  fast process; **kills on first-output timeout** (`sleep 300`, `first_output_timeout=1` →
  TIMEOUT_EXIT, err mentions "no output"/"first"); **kills on between-output timeout** (emit one
  line, then sleep, `idle_timeout=1` after a first byte → TIMEOUT_EXIT); **first byte flips N1→N2**
  (a script that prints immediately then runs 3s with `first_output_timeout=1, idle_timeout=10`
  survives); **records `t_first_event_s` and `max_gap_s`**; **total timeout still fires** even while
  output streams.
- [ ] **Step 3: Run tests — expect FAIL.**
- [ ] **Step 4: Implement `_run_streaming`.** Structure identical to `_run` for Popen, guard,
  signal handlers, and all three cleanup paths. Read loop uses `select.select()` on the
  stdout+stderr fds (`open_fds`); track `last_activity` (init = start) and `first_seen`. Check order
  each poll, BEFORE selecting — total FIRST so a genuine slow audit is never misread as idle:
  (1) `now-start >= timeout` → kill, reason `"review timed out after Ns"` (no "idle"); (2)
  `not first_seen and now-start >= first_output_timeout` → kill, reason `"idle timeout: no output within Ns"`;
  (3) `first_seen and now-last_activity >= idle_timeout` → kill, reason `"idle timeout: no output for Ns"`. Cap
  the `select` wait so the nearest deadline is not overshot. On a ready fd, `os.read`: **empty bytes
  = EOF → discard that fd from `open_fds`**; otherwise append and update `t_first_event`/`first_seen`,
  the running `max_gap` (from `now-last_activity`), and `last_activity`. **End the loop when
  `open_fds` is empty OR `process.poll()` is not None** — and on process exit, DRAIN any remaining
  readable bytes from both fds before breaking, so a completed review returns immediately instead of
  spinning to the total cap (an unread `select()` keeps reporting a closed pipe as readable). On
  non-POSIX, delegate to `_run` (no idle detection there). Extend
  `_record_invocation(argv, started, t_first_event_s=None, max_gap_s=None)`.
- [ ] **Step 5: Run tests — expect PASS.**
- [ ] **Step 6: Facade** — add the six state names for each timeout and `_run_streaming` handling
  (private, but referenced by tests via `rb`); regenerate the contract for the public ones.
- [ ] **Step 7: Commit** — `feat: add _run_streaming with first-output and between-output idle thresholds`

---

### Task 3: `/reviewerModel firstOutputTimeout` & `idleTimeout` commands

**Files:** modify `reviewer_model_toggle.py`; modify `commands/reviewerModel.md` (usage line); test
`tests/test_config.py` (or the model-toggle test).

- [ ] **Step 1: Failing tests** — `firstOutputTimeout 45` sets it; `idleTimeout 300` sets it;
  `... clear` reverts to default; a below-floor value is rejected with a clear error; `_show`
  displays both current values with their source (mirrors `_timeout_line`).
- [ ] **Step 2: Implement** — add `firstOutputTimeout`/`idleTimeout` subcommands to
  `reviewer_model_toggle.main`, each mirroring the existing `timeout` handler (parse, floor-check,
  `set_*`/`clear_*`, report). Add a display line each to `_show`. Reuse `_parse_timeout`'s shape
  with the per-knob floor.
- [ ] **Step 3: Run tests — expect PASS.** Update `commands/reviewerModel.md` usage/help.
- [ ] **Step 4: Commit** — `feat: set codex streaming idle thresholds via /reviewerModel`

---

### Task 4: Wire the codex commit audit to streaming (active by default)

**Files:** modify `reviewer_backends.py` (`resume_by_id_argv` schema/`-o`/`--json`;
`run_codex_streaming_audit`); modify `commit_hook.py` (codex branch); modify `tests/fake_codex.py`
(JSONL + schema-`-o` for resume); modify `reviewer_lib.py`; test `tests/test_commit_hook.py`.

**Interface produced:** `run_codex_streaming_audit(session_id, proj, sandbox, prompt, timeout,
first_output_timeout, idle_timeout, extra_config, on_cancel) -> (int, verdict_text, stderr)` — the
verdict_text is the reconstructed canonical string; callers use `is_approved` unchanged.

- [ ] **Step 1:** add `json_mode=False` and `output_schema=None`/`output_last_message=None` params
  to `resume_by_id_argv`; when set, insert `--json`, `--output-schema <path>`, `-o <path>` before
  the `resume` subcommand (verified they are accepted there).
- [ ] **Step 2: Update `fake_codex.py`** so that with `--json` AND `resume` in argv it emits the
  event stream AND, when `-o PATH` is present, writes the FULL schema JSON with ALL THREE fields
  `{"verdict":…,"findings":[…],"suggestions":[…]}` to PATH — a two-field fixture would fail the
  strict parser. Honor `FAKE_CODEX_*` knobs for verdict/findings/suggestions and exit code, and keep
  findings consistent with the verdict (APPROVED ⇒ findings empty) so the fixture passes the parser's
  consistency check. TWO delay knobs to target each threshold precisely: emit `thread.started` FIRST
  and flush, then apply optional `FAKE_CODEX_MID_DELAY` (sleep AFTER the first event — a genuine
  between-output gap that exercises **N2**), then the remaining events (`turn.started`,
  `agent_message`, `turn.completed` with usage); a separate `FAKE_CODEX_FIRST_DELAY` sleeps BEFORE
  any event (exercises **N1**).
- [ ] **Step 3: Implement `run_codex_streaming_audit`** — resolve N1/N2 (params or resolvers) and
  the total via `commit_review_timeout`. Compute ONE absolute `deadline = monotonic() + timeout` for
  the whole audit. Per attempt (unbounded except by budget and MAX_IDLE_RETRIES): create a
  **FRESH `-o` temp file** (unlink+recreate, so a retry can never read the previous attempt's
  verdict — the stale-`-o` finding); build argv via `resume_by_id_argv(..., json_mode=True,
  output_schema=codex_verdict_schema_path(), output_last_message=tmp)`; call `_run_streaming` with
  `timeout = max(0, deadline - monotonic())`. **Retry on an idle kill** (`code == TIMEOUT_EXIT
  and "idle" in err`, which the N1/N2 reasons carry and the total cap does NOT) as long as budget
  remains AND retries so far are below `MAX_IDLE_RETRIES = 8`: `reset_last_invocation` on each
  retry, and set `idle_retried` on the invocation to the retry count (integer, not boolean; see
  `docs/plans/codex_audit_retry_budget.md`). On `code == 0`: read the fresh `-o` file →
  `parse_codex_verdict_json` → `codex_verdict_to_text`; attach `turn_usage`. **Fail-closed return
  contract:** if `code == 0` but the verdict is missing/malformed (reconstruction is `""`), return
  `(NONZERO, "", "codex --json produced no schema-conforming verdict")` — a NONZERO code with an
  EMPTY verdict, NEVER `(0, <nonempty error>, …)`; `commit_hook` computes
  `completed = code==0 and out.strip()`, so exit 0 with any nonempty string would count as a real
  verdict and overwrite the preserved prior review (and could approve). Otherwise return
  `(code, verdict_text, err)`. Always delete the temp file(s) in a `finally`.
- [ ] **Step 4: Switch `commit_hook.py`** codex branch (line ~259) from `run_codex_resume_by_id` to
  `run_codex_streaming_audit` (same signature plus the two thresholds). `approved = code==0 and
  is_approved(out)` is unchanged.
- [ ] **Step 5: Tests** in `tests/test_commit_hook.py`: a normal codex review approves/rejects via
  the schema `-o` file (incl. APPROVED-with-suggestions and REJECTED-with-findings-and-suggestions
  reconstructed correctly); a first-output timeout retries and succeeds (`idle_retried` set); a
  between-output timeout retries; a double idle-timeout returns TIMEOUT_EXIT (fail-closed); `code==0`
  with a malformed/empty `-o` file returns a **NONZERO** code + empty verdict, so `completed` is
  False, the commit is blocked, and a previously saved verdict is PRESERVED (finding 7); an idle
  retry whose second attempt writes no verdict does NOT reuse the first attempt's `-o` (fresh file
  per attempt — finding 6); `turn_usage` captured. Update existing codex commit tests that assumed
  plain-text output.
- [ ] **Step 6: Facade + full suite** — add `run_codex_streaming_audit`; regenerate the contract;
  `python -m pytest tests/ -x`.
- [ ] **Step 7: Commit** — `feat: wire codex commit audits to schema streaming with dual-threshold retry`

---

### Task 5: Audit recording and log line

**Files:** modify `reviewer_audit.py` (`record_commit_attempt`); modify `commit_hook.py` (log line);
tests `tests/test_audit_record.py`, `tests/test_commit_hook.py`.

- [ ] **Step 1: Failing tests** — the audit record carries `t_first_event_s`, `max_gap_s`,
  `idle_retried`, and (when present) `turn_output_tokens`/`turn_input_tokens`/`turn_reasoning_tokens`/
  `turn_cached_tokens` from `turn_usage`; the log line carries `t_first`, `max_gap`, `idle_retried`.
- [ ] **Step 2: Implement** — read those fields from `last_invocation()` in `record_commit_attempt`
  (after `dur_s`); prefer `turn_usage` tokens over the stderr footer when present; add the fields to
  the `commit_hook` log format (after `dur_s`).
- [ ] **Step 3: Run full suite — expect PASS.**
- [ ] **Step 4: Commit** — `feat: record streaming timing, max-gap, and structured token usage`

---

## Verification

1. `python -m pytest tests/ -v` — all pass; `tests/test_facade_contract.py` carries every new name.
2. **Live smoke (schema):** the probe form
   `codex exec --json --output-schema codex_verdict_schema.json -o /tmp/v.json --sandbox read-only
   -C <repo> -c model="gpt-5.6-luna" "<prompt>"` writes schema-conforming JSON even when the model
   runs a tool. (Verified 2026-08-25 on 0.149.1.)
3. **Manual:** with reviewerAgent active, make a small commit — the log line shows
   `t_first=<~1-6s>` and `max_gap=<seconds>`; `~/.claude/reviewer-agent/logs/reviewer.log` records
   the timing and `turn_*` tokens.
4. **First-output hang (N1):** `REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS=5` + `FAKE_CODEX_FIRST_DELAY=30`
   (silent before any event) fires N1 in ~5s and retries while budget/cap allow.
5. **Between-output hang (N2):** `REVIEWER_IDLE_TIMEOUT_SECS=5` + `FAKE_CODEX_MID_DELAY=30` (emits
   `thread.started`, THEN goes silent) fires N2 and retries while budget/cap allow — a
   first-output delay would instead trip N1.
6. **Calibration:** collect `t_first_event_s` / `max_gap_s` from real audits; lower N2 toward the
   observed max legitimate gap (keeping headroom above the slowest test suite) and keep N1 small.
