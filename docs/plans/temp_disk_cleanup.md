# Temp files fill the disk — exploration + fix plan

**Status:** RESOLVED (2026-08-26). All three culprits are owned and fixed/assigned in the reviewed
project (the reviewed project's checkout) + its launcher cleanup script; **no `reviewer-agent-github` change needed.**
Kept as the record of the investigation and the ownership split. Measured 2026-08-26.

## Symptom

The APFS container is ~926 GB but only **~21 GB free** (sibling volumes/snapshots hold ~820 GB), so
the small shared headroom is exhausted within hours by temp churn from commit audits and test runs.
A second agent has been manually `rm`-ing temp dirs to keep going.

## The three culprits (in size order)

### 1. Reviewer-session scratch copies — the biggest, and NOT this harness's code
- Dirs like `/private/tmp/opstate-audit-parity.XXXXXX`, `/private/tmp/research-out-stage17.XXXXXX`,
  `opstate-parity-before/after`, `opstate-capture*`, `opstate-export-output.*`. Each is a full
  materialized copy of the **reviewed project's** tree, **1.6–1.8 GB apiece**. Several per audit (one
  per verification step), never removed. ~33 seen at once (~55 GB); ~7 more within a couple of hours.
- **Root cause / cloning answer:** reviewer-agent does NOT clone. Verified — the only copy in the
  harness is a single-file state migration (`reviewer_state.py:599`); no `copytree`/`git clone`/
  `worktree add`/`mktemp`, and `_run`/`_run_streaming` build the codex subprocess with no `env=`
  override (so no scoped `TMPDIR`). The audit runs in place: `codex exec -C <repo> --sandbox
  workspace-write … resume <id>`. **Corrected owner (per the reviewed project's developer, 2026-08-26):**
  the copies are made by the **codex reviewer session itself** — it copies the staged tree out to
  audit exactly what is being committed. NOT by the reviewed project's parity/export tooling: that
  reads the DB/JSONL in place, and a grep of their repo found no tree copy / `mktemp` except
  `run_codex_research.py`'s small per-task launcher staging (`mkdtemp`, not a tree copy). The copy is
  reasonable; **not cleaning it up is the leak.**
- **Fix = the reviewed project's brief (owner is doing it); NO harness change:**
  - (reviewed project — being added) One line in its `reviewerAgent.md` brief: run each verification
    in ONE scratch dir and `rm -rf` it after the verdict (or `trap`-clean on exit). This brief steers
    the reviewer session that makes the copies, so it fixes the leak at the source.
  - (this harness — considered, then rejected) A per-audit scratch dir + `TMPDIR=<that dir>` env on
    the codex subprocess + `rm -rf` in a `finally` would only contain scratch that honors `TMPDIR`
    (`mktemp -t`). The observed dirs are explicit `/private/tmp/opstate-*` paths, so they would NOT be
    redirected — harness `TMPDIR` containment does not catch them. The brief fix covers it; **no
    reviewer-agent-github code change is warranted.**

### 2. pytest temp — the reviewed (opstate) project's fixtures, ~5–20 GB per run — FIXED
- `/private/var/folders/.../T/pytest-of-<user>`. The opstate test suites build a scratch repo by
  copying the frozen input JSONL (`opstate.INPUTS`, tens–hundreds of MB) and the prompts dir **per
  test** (~60 tests/run); pytest keeps the last 3 runs' base temps. Measured 5.5–19.7 GB per run.
- **NOT in reviewer-agent-github** (confirmed: `grep -rl opstate tests/` here is empty).
- **FIXED by the reviewed project's developer (2026-08-26):** a 3-line `pytest.ini`
  (`tmp_path_retention_policy = failed`, count 1) — cheaper than the session-scoped fixture I
  proposed, and verified at **0 B after a green run** (down from ~25 GB). Supersedes the fixture idea.

### 3. Agent run directories — the agent framework, ~20 GB
- `/private/tmp/<run_id>/` with `work/ homes/ results/ status/ logs/`, 0.5–0.8 GB each, ~30 runs/day.
  `results/status/logs` are retained into the repo, but `work/` copies and codex `homes/` persist.
- **Not this harness.** Owner's plan (2026-08-26): the launcher is frozen and will NOT be changed;
  `work/` + `homes/` are removed once a run's `results/status/logs` are retained, folded into the
  existing cleanup script rather than touching the launcher.

## What is (and isn't) ours — RESOLVED

None of the three culprits are defects in `reviewer-agent-github`, and **no change to this repo is
warranted.** The considered harness contribution — best-effort `TMPDIR` containment for the reviewer
subprocess (culprit 1) — was rejected: the reviewer's copies use explicit `/private/tmp/opstate-*`
paths that do not honor `TMPDIR`, and the reviewed project's brief fix covers the leak at its source.
All high-value fixes live in the **reviewed project** (`research_out/`): its
`reviewerAgent.md` brief (culprit 1) and `pytest.ini` (culprit 2), plus its launcher cleanup script
(culprit 3).

## Resolution (2026-08-26, with the reviewed project's developer)

- **Culprit 1** — copies are the codex **reviewer session's own** (auditing the staged tree), not the
  reviewed project's tooling. Fix: a one-line `reviewerAgent.md` brief change (one scratch dir, clean
  up after the verdict) — owner is adding it. No harness change.
- **Culprit 2** — FIXED: 3-line `pytest.ini` (`tmp_path_retention_policy = failed`, count 1); verified
  0 B after a green run.
- **Culprit 3** — owner folds `work/`+`homes/` GC into the existing cleanup script; frozen launcher
  untouched.
- **Immediate relief:** a second agent is `rm -rf`-ing the accumulated `/private/tmp/opstate-*`,
  `/private/tmp/research-out-*`, stale `/private/tmp/<run_id>/work`+`/homes`, and `pytest-of-<user>`
  (all regenerable scratch).

Open questions from the prior draft are answered: opstate lives at the reviewed project's checkout and the developer
owns fixes 1–3 there; harness `TMPDIR` containment is not worth adding.
