# `/reviewerModel` for the pi backend

## Why

`/reviewerModel` is codex-only and, worse, it is *silently* codex-only. On a project whose backend
is `pi`:

- `/reviewerModel <model>` writes `state/codex_model`, which `reviewer_model("pi")` never reads
  (`reviewer_state.py:548-561` reads `REVIEWER_PI_MODEL` then `state/pi_model`). The operator sees a
  success line and nothing changes.
- bare `/reviewerModel` prints the codex model and effort, i.e. settings that are not in force.
- `effort` is pinned: `commit_hook.py:35` sets `PI_THINKING = "medium"` and passes it at line 125.
  **This is our gap, not a pi limitation.** `pi --help` documents
  `--thinking <level>` accepting `off, minimal, low, medium, high, xhigh, max`, and the reviewer
  already passes the flag — it just passes a constant. An earlier draft of this plan proposed
  REFUSING `effort` on pi on the assumption that pi had no such control; that assumption was
  wrong, and refusing a control the tool supports would have been a worse bug than the one being
  fixed.
- `timeout` DOES apply to pi (`commit_hook.py:124` passes `rl.commit_review_timeout()`).

Reporting configuration that is not in force is the "reads-as-complete-when-it-is-not" class this
codebase keeps finding. It is worse than an unimplemented feature because it actively misleads.

## Verified surface (read, not assumed)

| symbol | file:line | fact |
|---|---|---|
| `reviewer_model(backend)` | `reviewer_state.py:548` | **precedence differs per backend** — see below |
| `codex_model()` | `reviewer_state.py:130` | thin wrapper returning `codex_model_detail()[0]` |
| `codex_model_detail()` | `reviewer_state.py:105` | `(model, source)`; pi has no counterpart |
| `set_backend_model(backend, model)` | `reviewer_state.py:564` | exists, exported in `reviewer_lib.py:138`, called by NOTHING in the command layer |
| `clear_backend_model(backend)` | `reviewer_state.py:572` | same |
| `is_valid_codex_model` | `reviewer_state.py:136` | single plain token; already shared by both backends |
| `PI_THINKING` | `commit_hook.py:35` | hardcoded `"medium"`, not configurable |

**The backends do NOT share a precedence chain, and the new resolver must not flatten them:**

- **codex** — env `REVIEWER_CODEX_MODEL` > `state/codex_model` > **`config.json` `model`** > None.
  Three sources: `reviewer_model("codex")` delegates to `codex_model()`, which is
  `codex_model_detail()[0]`, and that reads the config key as its last candidate.
- **pi** — env `REVIEWER_PI_MODEL` > `state/pi_model` > None. Two sources; there is deliberately no
  `config.json` key for pi today, and this change does not invent one. A `piModel` key would add a
  source no existing code reads.

## Design

**One new resolver, mirroring the codex one, plus a backend branch in the toggle.** No new storage
format and no new config keys — `state/pi_model` already exists and is already read.

1. `reviewer_model_detail(backend)` in `reviewer_state.py`. For `codex` it delegates to
   `codex_model_detail()` unchanged, so no existing behaviour moves and the config-json candidate
   keeps working. For `pi` it walks `env REVIEWER_PI_MODEL` > `state/pi_model`, re-validating each
   candidate and skipping an invalid one, and returns `(model, source)` in the same shape. Display
   code must never restate precedence — that is the drift `codex_model_detail`'s docstring records.
   An unknown backend RAISES, matching `_backend_model_file`, so a typo cannot silently report the
   codex slot.

2. **Export `reviewer_model_detail` through `reviewer_lib.py`.** `reviewer_model_toggle.py` reaches
   everything via the `rl` facade, so an unexported resolver is unreachable from the command layer.
   `tests/test_facade_contract.py` pins the facade's exported names, so this is a contract change
   and the generated contract (`tools/gen_facade_contract.py`) must be regenerated, not hand-edited.

3. `reviewer_model_toggle.py` resolves the project's backend once and branches:
   - `_show()` names the backend it is reporting for, and reports THAT backend's model.
   - setting a model routes to `set_backend_model(backend, ...)`.
   - `clear` routes to `clear_backend_model(backend)`.
   - `effort <level>` and `effort clear` act on the backend's OWN effort slot, exactly as the model
     does. See item 6 — this is a second slot, not a refusal.
   - `timeout` is unchanged — it already applies to both (`commit_hook.py:124`).

4. **Effort becomes backend-aware too**, mirroring the model split rather than refusing:
   - `PI_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")` — pi's set from
     `pi --help`, which is NOT codex's set: pi has `off` where codex has `none`, and lacks `ultra`.
     Validating a pi level against `CODEX_EFFORTS` would accept `ultra` and `none`, and pi rejects
     an unknown level, which on a fail-closed gate blocks every commit. The sets must stay separate.
   - `reviewer_efforts(backend)` returns the valid set. `reviewer_effort_detail(backend)` returns
     `(effort, source)` over env `REVIEWER_PI_EFFORT` > `state/pi_effort` for pi, delegating to
     `codex_effort_detail()` for codex (which also reads `config.json` `reasoningEffort`).
   - **Validation happens at RESOLVE time, not only on set.** The toggle validates what it writes,
     but the env var and a hand-edited `state/pi_effort` bypass it entirely — and an invalid level
     reaching pi is not a cosmetic error: pi rejects it, the review fails, and the fail-closed gate
     blocks every commit until someone traces it back. Each candidate is re-validated against that
     backend's set and an invalid one is deterministically SKIPPED, exactly as
     `codex_effort_detail` and the model resolvers already do. `ultra` in `REVIEWER_PI_EFFORT` must
     therefore be ignored, not passed through.
   - **The effective pi default is `medium`, and `reviewer_effort_detail("pi")` says so.** Today's
     hook falls back to that constant, so an unset override is not "no setting" — it is `medium`.
     Returning `(None, "unset")` would let `/reviewerModel` print an ambiguous "no override" for a
     level that is definitely being sent. It returns `("medium", "default")` instead, and the
     display reports the effective level.
   - `commit_hook.PI_THINKING` stops being the source of truth: the hook resolves the configured
     level, preserving today's behaviour for anyone who sets nothing. `_effort_label` must report
     the level ACTUALLY used, since its docstring says drift there corrupts cost/catch-rate
     comparisons.
   - An invalid level is rejected on set with the backend's own list in the message.

5. **The effort slot needs a full backend-generic API, not just a resolver.** The model split had
   `set_backend_model`/`clear_backend_model` already written (`reviewer_state.py:564,572`); effort
   has NO equivalent — only `set_codex_effort`/`clear_codex_effort`. So this change must add:
   - `pi_effort_file()` — `state/pi_effort`, mirroring `pi_model_file()`;
   - `_backend_effort_file(backend)` — dispatch, RAISING on an unknown backend exactly as
     `_backend_model_file` does, so a typo cannot silently land in the codex slot;
   - `set_backend_effort(backend, effort)` — validates against `reviewer_efforts(backend)`, so pi
     cannot be given `ultra` and codex cannot be given `off`;
   - `clear_backend_effort(backend)`.

   **Export all six new names** (`reviewer_efforts`, `reviewer_effort_detail`, `pi_effort_file`,
   `set_backend_effort`, `clear_backend_effort`, and `reviewer_model_detail`) through
   `reviewer_lib.py`: the toggle and `commit_hook.py` reach state only through the `rl` facade and
   `tools/check_split_layering.py` enforces it, so an unexported name is either unreachable or a
   layering violation. Regenerate the facade contract with `tools/gen_facade_contract.py` — it is
   generated, never hand-edited.

6. The project for backend resolution is `rl.project_dir()`, which the toggle already uses.

7. **Documentation updated in the same commit**, because all of it currently states the opposite:
   - `commands/reviewerModel.md` — the description line, the codex-scoped paragraph, and the
     `Levels:` list, which currently prints only `CODEX_EFFORTS`. It must show each backend's own
     set and name the difference (`off` vs `none`, no `ultra` on pi);
   - `README.md` — the Backends table's `model` and `reasoning level` rows, the `REVIEWER_PI_MODEL`
     configuration row, and a NEW `REVIEWER_PI_EFFORT` row documenting the override, its valid set,
     and that an invalid value is skipped rather than rejected loudly;
   - the README backlog entry for this item is removed once it is true.

## Tests (written first)

- setting a model on a pi project writes `state/pi_model`, NOT `state/codex_model`;
- setting a model on a codex project still writes `state/codex_model` (no regression);
- `_show()` on a pi project reports the pi model and does not print the codex model;
- `_show()` names the backend, so the output cannot be misread as global;
- `effort <level>` on a pi project writes `state/pi_effort`, NOT `state/codex_effort`;
- `effort clear` on a pi project clears the pi slot and leaves a previously-set codex effort intact;
- `effort ultra` is REJECTED on pi (codex-only level) and `effort off` is ACCEPTED on pi;
- `effort off` is REJECTED on codex and `effort ultra` accepted, i.e. the sets do not leak;
- **`REVIEWER_PI_EFFORT=ultra` is SKIPPED by the resolver**, which falls through to the state file
  or the `medium` default rather than passing a level pi would reject;
- **a hand-edited `state/pi_effort` containing `none` is likewise skipped**, since the toggle's
  set-time validation cannot police a file written by hand;
- `reviewer_effort_detail("pi")` with nothing set returns `medium` with a `default` source, and a
  bare `/reviewerModel` on a pi project reports that effective level rather than "no override";
- the commit hook passes the RESOLVED pi level to `run_pi_review`, and `medium` when unset;
- `_effort_label("pi")` reports the resolved level, not a constant;
- `reviewer_efforts("pi")` and `reviewer_efforts("codex")` differ, and every new name is reachable
  through the `rl` facade (the regenerated contract test covers presence);
- `set_backend_effort("pi", "ultra")` and `set_backend_effort("codex", "off")` both raise, so the
  setter enforces the split rather than relying on the toggle to pre-check;
- `set_backend_effort("nonsense", "low")` raises rather than writing the codex slot;
- `clear` on a pi project clears the pi slot and leaves the codex slot intact;
- an invalid pi model is skipped by the resolver, and `reviewer_model_detail("pi")` reports the
  next source rather than the invalid one;
- `reviewer_model_detail("codex")` agrees with `codex_model_detail()` for the same state, including
  the `config.json` `model` candidate;
- `reviewer_model_detail("nonsense")` raises rather than defaulting to codex;
- the facade contract test passes with the new export present.

## Subsequent work

This plan originally left `AskUserQuestion` answering and stop supervision out of scope. That
follow-up is now implemented: Pi has independent reviewer and stop role slots, and the selected
Claude Code backend handles commits, timed-out questions, and stop supervision.
