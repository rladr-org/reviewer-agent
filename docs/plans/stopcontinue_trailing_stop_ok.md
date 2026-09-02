# Plan: accept a trailing `STOP_OK` verdict (stopContinue)

Bug report (reviewer-agent 1.2.1-beta.2, Claude Code host, `claude` backend, Linux): a `STOP_OK`
verdict is discarded unless it is the first line of the supervisor's reply.

## Problem

The stop supervisor concluded the task was complete and answered `STOP_OK` — but only after
explaining itself, so the sentinel was the LAST line of its reply. `is_stop_ok` reads the first
non-empty line only, so the permit-the-stop verdict was read as a continue directive. The agent was
nudged six times on a finished task, each round costing ~45 s of supervisor latency, until the
nudge cap released it; its final messages shrank (5282 → 350 chars) as it ran out of things to say
and began arguing with the hook. Two consecutive verdicts from `logs/reviewer.log`, both recorded
`keep_going=True`, ended:

```
... The operator's stopping condition ("all ten boxes ticked and placeholder text gone") is
fully met, and there is no authorized work left to do.\n\nSTOP_OK
... There is no authorized work left incomplete.\n\nSTOP_OK
```

`STOP_SUPERVISE_PROMPT` says "Reply with exactly one of: STOP_OK, and nothing else", so the
supervisor was non-compliant. What the plugin does with that non-compliance is the defect:

1. **It fails in the wrong direction.** `stop_hook.py` documents the path as fail-open: any backend
   failure, an empty final message, or a `STOP_OK` verdict lets the stop proceed. A `STOP_OK` in
   the wrong position is the one case that fails closed and loops the agent.
2. **The commit path already guards the analogous shape; the stop path does not.** `is_approved`
   has a contradiction check whose rationale is "prompt and parser must name the same token, or
   this is the same defect one level down." `is_stop_ok` is that defect one level down.
3. **It is invisible in the log.** `keep_going=True` is recorded identically whether the supervisor
   said "keep working" or said `STOP_OK` and was ignored — and the directive relayed to the agent
   is the full reasoning text *including the trailing `STOP_OK`*, so the agent receives a continue
   instruction whose body says there is nothing to do. That is what made it dispute the hook.

The same first-line-only rule exists in the native Codex package
(`plugins/reviewer-agent-codex/reviewer_codex/stop_tool.py::_stop_ok`), with the exact-token
variant. reviewerAgent.md requires the two stop supervisors to stay aligned, so both are fixed.

## Decision: where a `STOP_OK` counts

A reply permits the stop when the sentinel is in either of two positions:

- **first** — the first non-empty line opens with the whole word `STOP_OK` (Claude Code path:
  `STOP_OK -- the task is genuinely complete.` stays valid; `STOP_OKAY`, which the old bare
  `startswith` accepted, no longer counts); the Codex package keeps its exact-token rule on this
  line (`STOP_OKAY` stays rejected, and the trailing-line tolerance does not widen it).
- **last** — the last non-empty line is the *bare* token: `STOP_OK` alone, tolerating only markdown
  emphasis/backticks around it and trailing sentence punctuation (`**STOP_OK**`, `` `STOP_OK` ``,
  `STOP_OK.`; not quote wrappers such as `"STOP_OK"`). This is the observed shape: a model that
  explains itself and then answers.

The trailing position is deliberately **exact-token, not prefix**: a continue directive can end
with a sentence such as "STOP_OK is not warranted until the tests pass", and a prefix rule would
read that as approval. Prefix matching stays confined to the first line, where the prompt puts the
verdict and where the existing tests pin it.

A bare `STOP_OK` line in the **middle** of the reply — followed by more prose — is **not**
honored. That reply both permits the stop and keeps directing; it is the stop-path analogue of the
self-contradictory `APPROVED ... REJECTED` first line the commit gate refuses. The supervisor's job
is to keep the agent working, so the contradictory reading resolves to "continue" — but it is
**logged distinctly** (below) so it can never again be silently inverted. This is the one shape
the report's "a line of its own anywhere" suggestion would accept and this plan does not; the
report's observed failure is the trailing shape, which is accepted.

## Changes

### `reviewer_stopcontinue.py`

- New `stop_ok_position(codex_out) -> "first" | "last" | "mid" | None` — the single classifier.
  `"mid"` means a bare sentinel line exists that is neither first nor last (not honored).
- `is_stop_ok(codex_out)` becomes `stop_ok_position(...) in ("first", "last")`. Same signature,
  same truthiness for every input the existing tests cover; the change is purely additive on the
  trailing shape.
- Re-exported through the `reviewer_lib` facade (regenerate `tools/gen_facade_contract.py`).

### `stop_hook.py`

- The `[stop]` log line gains `stop_ok=<first|last|mid|none>` next to `keep_going=`, so a
  `keep_going=True stop_ok=mid` (contradictory reply resolved to continue) and a
  `keep_going=False stop_ok=last` (trailing verdict honored) are both visible. No behaviour change
  beyond what the parser now returns: a trailing `STOP_OK` takes the existing approval branch
  (echoed to the operator, arms the watchdog idle countdown, pre-enactment delay applies).

### `plugins/reviewer-agent-codex/reviewer_codex/stop_tool.py`

- `_stop_ok` accepts the bare token on the first OR last non-empty line, same tolerance for
  emphasis/punctuation, same rejection of `STOP_OKAY` and of a mid-reply sentinel. Result shape
  unchanged (`stop_ok` with `ruling=<full text>`; `incomplete` with `directive=<full text>`).

### Docs

- `CHANGELOG.md` — `### Fixed` entry in the open `1.2.1` pre-release section, released as
  `1.2.1-beta.4` (a bump is what lets a directory-source install pick the fix up; `1.2.1-beta.3`
  was declared but never tagged).
- Docstrings on both parsers state the two positions and the excluded middle position.

## Tests (red first)

`tests/test_reviewer_lib.py`:
- trailing bare `STOP_OK` after reasoning → `True`; with `**STOP_OK**`, `` `STOP_OK` ``,
  `STOP_OK.` → `True`; lowercase → `True`.
- trailing *prefix* (`STOP_OK is not warranted yet.`) → `False`.
- bare `STOP_OK` in the middle followed by a directive → `False`, `stop_ok_position == "mid"`.
- `stop_ok_position` returns `"first"` / `"last"` / `None` on the corresponding inputs and `None`
  for empty / whitespace input.
- the existing `test_is_stop_ok_recognizes_the_sentinel` cases stay green unchanged.

`tests/test_stop_hook.py`:
- end-to-end: `FAKE_CODEX_STDOUT` = reasoning + `\n\nSTOP_OK` → no `decision` in the output, the
  verdict is echoed in `systemMessage`, the nudge counter is NOT bumped, and the `[stop]` log line
  carries `stop_ok=last`.
- end-to-end: a mid-reply sentinel → `decision == "block"` and the log line carries `stop_ok=mid`.

`plugins/reviewer-agent-codex/tests/test_stop_tool.py`:
- reasoning + trailing `STOP_OK` → `status == "stop_ok"` and the stop permit is granted.
- trailing prefix (`STOP_OK is not warranted`) and mid-reply sentinel → `status == "incomplete"`.
- the existing `test_stop_ok_requires_exact_whole_token` stays green.

`tests/test_facade_contract.py` — regenerated so `stop_ok_position` is part of the contract.

## Not changed

- `STOP_SUPERVISE_PROMPT` / `STOP_BRIEF` wording: the prompt already asks for the bare token, and
  the fix is to tolerate a well-formed but mispositioned answer, not to invite prose. Adding "put
  STOP_OK first" would be a third statement of the same instruction the model already ignored.
- The nudge cap, the pre-enactment delay, and the watchdog arm/disarm logic: they consume the
  parser's verdict and are unaffected.
