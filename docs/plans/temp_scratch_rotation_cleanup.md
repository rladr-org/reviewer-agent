# Session-scoped reviewer scratch + rotation cleanup + warn-only disk reporting

**Status:** planned 2026-08-26; NOT yet implemented. Author to execute with TDD. Deploy is gated by
the same stale-live-tree sync decision as codex-streaming (do not deploy independently).

## Why (the general case)

The per-project fixes for the disk fill (`docs/plans/temp_disk_cleanup.md`) are project-local: they
stop *that project's* leak but not the next reviewed project's. The reviewer session itself copies the
staged tree out to audit it, and that scratch is never cleaned up. This adds a **harness-side** guard
that applies to *every* reviewed project: contain the reviewer's scratch in a session-scoped dir,
dispose it at rotation (not mid-session), sweep orphans on startup, steer the reviewer to use it, and
**warn (never block)** when the temp volume is low.

## Design decisions (settled before coding)

### Keying: by `reviewer_session_id` (PER-GENERATION), NOT by `(proj, claude_session)`
The scratch dir is `<root>/<sanitize(reviewer_session_id)>`. Rationale (this is the review-mandated
fix for the rotation/concurrency hazard):
- **Per-generation isolation.** The reviewer_session_id changes on every rotation, so each reviewer
  generation gets a unique path. Rotation removes ONLY the retired generation's dir; the next reviewer
  is briefed under a NEW id and therefore a NEW path — it can never recreate or reuse a path a
  concurrent//just-rotated audit still holds. Keying by `(proj, claude_session)` was rejected exactly
  because that path is SHARED across generations: rotation could delete a dir a concurrent audit is
  using, and the next reviewer would alias the same path.
- **Reuse within a session preserved.** The id is STABLE across a session's audits (only rotation
  mints a new one), so all of one reviewer's audits share one dir (the "reuse within a session, not
  per-audit" requirement).
- **Known at audit time.** Every audit resumes BY id (`resume <id>`), so the id — and the path — is in
  hand when the audit subprocess is launched. See the injection note for the create-time subtlety.

### Root: under the system temp base, NOT under the state home
Root = `Path(REVIEWER_SCRATCH_ROOT or tempfile.gettempdir()) / "reviewer-agent-scratch"`. Rationale:
the codex reviewer runs `-C <repo> --sandbox workspace-write`; the state home (`~/.claude/reviewer-
agent`) is OUTSIDE that workspace and not reliably writable by the sandboxed child. The system temp
base IS writable under workspace-write (evidence: the reviewer already writes `/private/tmp/opstate-*`
and codex's `-o` verdict file lands in the temp base). `REVIEWER_SCRATCH_ROOT` override exists for
tests and operators.

### Injection: `TMPDIR` on the AUDIT subprocess only (not the brief)
Set `env = {**os.environ, "TMPDIR": str(scratch_dir)}` on the audit subprocess. Threaded as an
optional `scratch_dir=` param `commit_hook → run_codex_streaming_audit → _run_streaming → Popen`.
Backends stay generic (they just forward `scratch_dir` into `env`); the id→path mapping is computed at
the call site, which has the reviewer_session_id (`sid`). The harness's own `-o` `mkstemp` runs in the
harness process, so it is unaffected — it keeps landing in the harness temp and is unlinked in its
`finally`.
**Create-time subtlety:** the briefing call that CREATES the session cannot be scoped by id (codex
assigns the id only as the brief runs). That is acceptable: the brief runs only the system prompt and
creates negligible scratch (the heavy tree-copies come from the audit tooling, which IS scoped). So
`TMPDIR` is set on the audit resume subprocess only; brief-time scratch (negligible) is unscoped —
documented minor gap.

### Liveness = an mtime lease, NOT the session-ID files
The orphan sweep's notion of "still in use" is the scratch dir's mtime, renewed on every audit
(`commit_scratch_dir(create=True)` touches it). It must NOT be the backend session-ID files
(`.codex`/pi/claude): those persist after a Claude session ends (only rotation unlinks them), so
using them as a liveness signal would mark every session that ever ran as live and keep its scratch
forever. A dead session stops renewing its lease; the next SessionStart sweep reclaims it once the
lease is older than `REVIEWER_SCRATCH_TTL_S` (default 12 h, configurable). The TTL is generous so a
live session with a long gap between commits is not reaped mid-work; if it ever is, the next audit
just recreates an empty dir (scratch is regenerable — correctness is unaffected, only reuse). Rotation
(Task 3) still deletes deterministically for the common case; the lease sweep is the backstop for
sessions that end WITHOUT rotation (crash, closed window).

### Residual gap (documented, mitigated not closed)
`TMPDIR` containment only catches scratch that honors `$TMPDIR` (`mktemp` without an explicit path).
A reviewer/tool that hardcodes `/private/tmp` escapes it. The **prompt guidance** steers the reviewer
to keep scratch under `$TMPDIR`; the **free-space warn** is the catch-all for whatever still leaks.

### Free-space reporting is WARN-ONLY (operator-confirmed)
Never block a commit on low disk — a full shared volume would otherwise wedge every commit. At audit
time check `shutil.disk_usage(<nearest existing ancestor of the scratch root>).free`; if below
`REVIEWER_DISK_WARN_BYTES` (default 2 GiB) emit one `[reviewerAgent] low disk: …` line to the operator
output and `log()`. The whole check is wrapped in `try/except Exception` and is strictly inert to the
verdict/exit — see Task 5 (the scratch root may not exist for a non-codex audit).

## Tasks (TDD — test first, then implement, suite green at each step)

1. **Scratch lifecycle helpers** (`reviewer_state.py`, co-located with the session id + rotation):
   - `commit_scratch_root()` → the root Path (honors `REVIEWER_SCRATCH_ROOT`).
   - `scratch_key(reviewer_session_id)` → `_sanitize(reviewer_session_id)` or None on a falsy id
     (the pi:/claude: prefixes sanitize fine; ids are globally unique so no project prefix needed).
   - `commit_scratch_dir(reviewer_session_id, create=False)` → `<root>/<scratch_key>` or None on a
     falsy id. `create=True` `mkdir(parents=True, exist_ok=True)` AND **renews the lease** by
     refreshing the dir's mtime (`os.utime(dir, None)`), so every audit stamps the dir recently-used.
   - `remove_commit_scratch_dir(reviewer_session_id)` → best-effort `shutil.rmtree(...,
     ignore_errors=True)`; never raises; None/absent id is a no-op.
   - `sweep_orphan_scratch_dirs(max_age_s, protect_keys=())` → removes every dir under the root whose
     **mtime is older than `max_age_s`** — an mtime LEASE, refreshed on each audit. Liveness is
     deliberately NOT derived from the backend session-ID files (`.codex`/pi/claude): those persist
     after a session ends (only rotation unlinks them), so a session-file signal would treat every
     session that ever ran as live and keep dead scratch forever. `protect_keys` (e.g. the current
     reviewer's `scratch_key`, when one is live) is an extra guard skipped regardless of mtime.
     Best-effort; never raises.
   - Tests: path shape, falsy id → None, create makes it (and renews mtime), remove is idempotent;
     an ENDED session whose backend ID file STILL EXISTS but whose scratch mtime is older than the
     TTL **is removed** (the finding's case); a fresh/recently-used dir (mtime within TTL) is KEPT;
     `protect_keys` keeps a named key even past the TTL.

2. **Backend `TMPDIR` injection** (`reviewer_backends.py`): add `scratch_dir=None` to `_run`,
   `_run_streaming`, and `run_codex_streaming_audit`; when set, pass `env={**os.environ, "TMPDIR":
   str(scratch_dir)}` in `popen_kwargs`. Tests patch `subprocess.Popen` and assert the child `env`
   carries `TMPDIR=<dir>` when passed and is unchanged (no `env=`) when not.

3. **Wire the commit audit + rotation cleanup** (keyed by `reviewer_session_id`):
   - `commit_hook.py:264`: compute `scratch_dir = rl.commit_scratch_dir(sid, create=True)` (where
     `sid` is the reviewer session id already in scope) and pass `scratch_dir=` to
     `run_codex_streaming_audit`.
   - `retire_commit_reviewer_session(proj, claude_session, reviewer_session_id, …)`: it already has
     the id → `remove_commit_scratch_dir(reviewer_session_id)` alongside the state unlinks.
   - `rotate_session(proj, claude_session)`: has no id param → READ the current reviewer-role id first
     (`read_session_id`/`read_pi_session`/`read_claude_session` for `ROLE_REVIEWER`) BEFORE it unlinks
     the session file, then `remove_commit_scratch_dir(that_id)`. Both removes are best-effort.
   - **Why this is race-safe (the finding):** because paths are per-generation, rotation removes only
     the retired id's dir; the next reviewer gets a different id → a different path, so it never
     recreates or reuses a path a concurrent audit holds. Auto-rotation additionally runs AFTER the
     audit subprocess has exited (`commit_hook.py:356` is post-`run_codex_streaming_audit`), so its
     own scratch is already idle when removed.
   - Tests: auto-rotation (`record_completed_reviewer_audit` at the limit) removes `<root>/<key(sid)>`;
     explicit `rotate_session` removes the current reviewer id's dir; a non-rotating audit leaves it;
     **concurrent-rotation test** — rotating session S removes only S's dir, and a freshly briefed S2
     resolves to a DIFFERENT path (no aliasing/reuse of S's removed path).

4. **Orphan sweep on session start (UNCONDITIONAL)**: run `sweep_orphan_scratch_dirs(
   REVIEWER_SCRATCH_TTL_S, protect_keys=<current reviewer id's key if one is live, else empty>)` on
   EVERY SessionStart. It must NOT be gated on there being an active codex session — the existing
   `retire_legacy_sessions` call is reached only for active codex sessions, so the sweep is scheduled
   independently (before/outside that branch), so it also runs for inactive, Pi, and Claude sessions.
   Liveness is the mtime lease (Task 1), so an ended session — whose backend ID files linger — is
   reclaimed once its lease expires, regardless of backend (`protect_keys` is only a belt; a live
   session's dir is within-TTL anyway). Tests: the sweep runs when there is NO active codex session; a
   stale-lease dir (backend ID file still present) is removed; a within-TTL dir is kept.

5. **Warn-only free-space check** (`commit_hook.py`, in the audit path) — robust and inert to the
   verdict:
   - Measure free space on the scratch root's **nearest existing ancestor**: `commit_scratch_root()`
     if it exists, else its first existing parent (ultimately the temp base). The root may not exist
     for a non-codex audit that created no scratch dir, so `shutil.disk_usage(root)` must not be
     called on a missing path.
   - Wrap the ENTIRE check in `try/except Exception` and swallow — a stat/FS error must never
     surface. Emit at most one `[reviewerAgent] low disk …` line to operator output + `log()`.
   - The check MUST NOT read or write `code`/`completed`/`approved` or influence the exit path; it
     only prints. Threshold `REVIEWER_DISK_WARN_BYTES` (default 2 GiB).
   - Tests: below threshold warns AND the commit proceeds byte-for-byte unchanged; above threshold
     silent; a MISSING root neither raises nor warns spuriously (falls back to an existing ancestor);
     the verdict and exit code are identical with and without the warning.

6. **Prompt guidance**: one line in the commit audit prompt (`COMMIT_PROMPT` / `compose_brief` /
   capsule — whichever the audit uses) instructing the reviewer to do verification work inside a
   single dir under `$TMPDIR` and remove it, leaving no tree copies behind. Test asserts the composed
   commit prompt contains the `$TMPDIR` guidance.

7. **Facade + suite**: `python3 tools/gen_facade_contract.py` after adding the new `rl.*` callers;
   full `pytest` green (currently 1007 passed, 3 skipped).

## Out of scope / explicitly rejected
- Per-audit `finally` cleanup (would wipe a persistent session's reusable scratch mid-session).
- Blanket "snapshot `/private/tmp`, delete new entries" (unsafe with concurrent audits/agents).
- Hard-blocking on low disk (warn-only, per operator).
