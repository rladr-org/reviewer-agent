# Codex Linux findings: locally implementable fixes

## Scope and review sequence

Address issues #1, #2, #3, #5, #6, and #7 from the operator-provided Codex Linux
issue report. Issue #4 (command detection and complete mediation of indirect Git
operations) needs a separate enforcement design and is excluded from this batch.
The independent Claude runtime findings are also excluded; shared installation and
contributor documentation is included where the Codex report identifies it.

An implementation was drafted and locally tested before the required staged commit
reviews. That was a procedure error. This sequence submits each stage independently
from an isolated linked worktree, without rewriting existing history:

1. Commit this plan through reviewer-agent.
2. Add and run meaningful failing regression tests against the unchanged runtime,
   then commit the intentionally red tests through reviewer-agent.
3. Implement the fixes, show the regressions and applicable suites green, bump the
   Codex cache version, and commit the implementation through reviewer-agent.
4. Integrate the reviewed commits while preserving unrelated shared-checkout work.
5. Install using the checkout installer, inspect the installed package, and run
   installed-code and live-host checks where available. Record actual outcomes and
   any host-trust/restart or Linux-only acceptance still pending.

No stage bypasses the reviewer gate. The red-test commit intentionally has failing
new tests; those failures establish the defects to fix in the next commit. Test
repair for #3 is split too: first add a deliberate delayed startup that exposes the
race, then add the bounded readiness handshake in the implementation commit.

## Changes

### #1: Canonical project identity on old Git

In `reviewer_codex/state.py`, use plain `git rev-parse --git-common-dir` and resolve
relative output against the queried repository root, not the plugin process cwd.
Keep canonical absolute identities, sharing between linked worktrees, and isolation
between unrelated repositories. Retain the existing Git-failure fallback behavior.

Tests emulate old Git's successful unknown-option echo, exercise relative and
absolute common-directory output from an unrelated cwd, and exercise real linked
worktrees. Run the existing state-isolation suite, including history/state consumers.

Do not migrate colliding historical records: ownership can be ambiguous. Document
backing up and inspecting/quarantining affected state, deliberately reconfiguring
each repository, and never copying old tickets or stop permits into corrected keys.

### #2: Portable test environment

Replace the mandatory `amygdala` conda environment and corresponding contract-test
assertion with an available Python 3.9+ interpreter, a virtual environment when
needed, root `requirements-dev.txt`, and `python -m pytest -p no:cacheprovider`.
Keep TDD, standard-library-only runtime, and existing commit/stop invariants.

Update package `AGENTS.md`, `reviewerAgent.md`, the contract test, and the named
references in `AGENT_UPDATE.md`, `HANDOVER.md`, and the Claude reviewed-commit plan.
State working directories explicitly. Creating a named environment is not the fix.

### #3: Separate test startup from the cleanup deadline

The timeout test in package `tests/test_backend.py` must first wait for both the
PID marker and a heartbeat from the detached descendant within a bounded startup
budget. Only then start its real 0.5-second communication timeout. Keep production
`backend._run` timeout semantics unchanged by using a test-local adapter.

Exercise immediate startup, deliberately delayed startup beyond 0.5 seconds, and
startup-budget expiration. Continue asserting the real timeout result and stopped
heartbeat. Cleanup/reap test processes on success and assertion failure. The report
establishes test flakiness, not a demonstrated runtime cleanup defect.

### #5: Explicit denial for gate and launcher failures

Validate relevant hook input types. Put plugin imports/bootstrap and gate execution
inside an exception boundary in `scripts/hook_pre_tool.py` that can emit JSON deny
without importing the failed plugin or writing state. Keep diagnostics free of raw
command contents. Preserve optional null inputs and unrelated tool inputs.

Add a shell-level exit-2 fallback to the PreToolUse command in `hooks/hooks.json` so
missing Python, missing script, and nonzero process failures block explicitly. Keep
stdin, quoting, and cache-eviction fallback behavior. Installation must check the
PATH-selected `python3` runs Python 3.9+ before writing configuration. Clarify that
an installer's PATH does not establish an already running Codex host's environment.

Keep stop-supervision plan/liveness storage failures best effort; do not turn a
bookkeeping failure into denial of a non-commit command. Cover malformed JSON and
field types, imports, obstructed ticket storage, interpreter/script failures,
launcher crashes, optional inputs, and healthy unrelated operations in fixtures.

The official [Codex hook contract](https://learn.chatgpt.com/docs/hooks) describes
PreToolUse JSON deny and exit 2 with a stderr reason as blocking responses. The
failure boundary cannot cover untrusted/missing hook registration or a host killing
the whole launcher on timeout. Do not equate subprocess results with live-host proof.

### #6: Model-facing brief template

Move copy/enable instructions out of the Codex template into install documentation.
At enablement, reject unreadable/empty briefs and the known unedited project-guidance
placeholder with an actionable message before recording acceptance/enabling review.
Preserve customized briefs and the complete composition of legitimate instructions.
Document manually removing older human-facing preambles; do not strip arbitrary
text before a Markdown separator or rely on HTML comments to hide text from a model.

### #7: Shared installation instructions

Update `INSTALL_WITH_AGENT.md`, relevant `AGENT_INSTALL.md` setup directions, and
the Codex README consistently. Explain authentication failure handling for any
configured source, without inferring repository visibility from a clone failure.
Preserve the working transport/key selection for later updates without logging secrets.

Require a permanent user-owned checkout outside the protected repository. Explain
Codex's source symlink and update/fallback dependency even when an immutable cache
continues working. Give dependency, interpreter, cwd, and test commands before
installation. A temporary test venv is fine; the plugin source checkout is permanent.

## Validation and remaining platform work

Use targeted red tests, targeted green tests, then the full Codex suite. Run shared
document/distribution/export/license/release-metadata checks for the shared edits,
plus layering, version consistency, and release-content checks. Exercise the Python
3.9 floor where a suitable interpreter is available. Use isolated test state and fake
reviewer backends in deterministic tests; only explicit live checks use model calls.

After approved implementation, use the plugin-creator cachebuster flow and the
repository's `scripts/install_local.py`, which preserves live cache directories and
configures the direct reviewer MCP namespace. Compare installed bytes to committed
source, inspect the advertised tools/hooks, and exercise installed hook regressions.
Use a fresh host session to establish live hook behavior; never manually call Stop
or Interrupt lifecycle tools. Preserve feature settings and operator risk acceptance.

Linux remains preferable for actual Git 2.25.1 compatibility, `/proc` descendant
cleanup under CPU load, reported CLI/PATH failure reproduction, and a fresh Linux
installation. These follow-ups, including an old-Git CI job, are not prerequisites
for the local source fixes. Record them as pending rather than claiming local mocks
prove them. #4 remains open for a separately reviewed enforcement design.
