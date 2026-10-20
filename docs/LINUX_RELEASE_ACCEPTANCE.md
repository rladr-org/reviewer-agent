# Linux release acceptance: Codex and Claude Code

Use this runbook for the final Linux acceptance of the **unpublished 1.4.3 candidate**.
It is for agents running inside Codex or Claude Code, with an operator available for interactive
checks. A successful script run alone does not establish that a host loaded or invoked its hooks.

## Instructions to either testing agent

Read this file and [INSTALL_WITH_AGENT.md](../INSTALL_WITH_AGENT.md) completely. Execute the
shared checks and your host's section; record every result using the template below. Read the
linked host guide before installing. Do the work you can perform yourself, and group the
remaining operator actions into short checkpoints. Do not infer that an operator action passed.

- Test the **full candidate commit SHA supplied by the maintainer**, not a moving `main`.
  Record that SHA, the source versions and the Codex build suffix before starting. If no SHA
  was supplied, resolve `origin/main` once, report it, and have both agents use that same SHA.
  Do not pull, bump versions, refresh the cachebuster or change source during the run.
- Use separate disposable target repositories for the two hosts. Keep the installer checkout
  and any installed export in permanent, user-owned directories **outside** the targets.
  Start the developer host in its target: changing a shell subprocess's directory does not
  change the host's project identity. Never run fixture commits in the release repository.
- Coordinate shared checks: one agent may run both suites and artifact checks; the other may
  reuse the report only for the same SHA, Linux machine, interpreter and temporary filesystem.
  Do not concurrently install/uninstall or change shared settings. No extra test profile is
  required; preserve unrelated configuration, plugins, risk receipts and existing project briefs.
- The operator chooses the Claude reviewer backend and authorizes live calls, which send test
  context to that provider and consume quota. Use synthetic fixture code, not private projects.
  Authentication, exact `acceptRisk` commands, hook trust, host restart and keyboard interaction
  belong to the operator. Respect the host's normal filesystem/network approval mechanism.
- Do not fix a defect in this acceptance run. Capture a minimal reproduction, expected/actual
  behavior and sanitized evidence. A fix creates a new candidate and requires affected checks
  to be rerun. Do not push, tag, publish, change repository visibility or create GitHub issues.

## Update, restart and fresh-install order

On an existing installation, first follow [AGENT_UPDATE.md](../AGENT_UPDATE.md) and the host's
update guide. Record and correct errors or missing steps in a separate documentation checkout;
keep the runtime artifact and its candidate identity frozen. Confirm existing effective settings
with the operator using the update guide; after reload, compare the settings with their answer.
For fresh installation, verify the agent asks about model, effort, timeouts and applicable optional
settings in understandable groups with purpose/trade-offs, and explains hook trust versus risk
acceptance before giving enablement commands. Verify the agent waits for each group's answer
unless the operator chooses a combined confirmation; unanswered choices stay pending.
Complete update verification, then
ask the operator to trust/restart the host and perform the automated host and manual checks.
Only after those checks, follow the applicable [uninstall procedure](../AGENT_UNINSTALL.md)
(including its fresh-host removal checkpoint), then exercise
[INSTALL_WITH_AGENT.md](../INSTALL_WITH_AGENT.md) from the start. Execute the documented commands
directly and record each command's exit status separately from verification results. Do not create
a temporary orchestration script or automatically reinstall in a cleanup/finally block. If the
procedure has a gap, correct the documentation in the separate checkout before continuing.
Retain before-uninstall, after-uninstall and after-install evidence; a boolean-only summary cannot
explain a discrepancy after reinstall overwrites the intermediate state. Expected removal of the
plugin's own configuration and an otherwise empty parent table is not loss of unrelated settings;
compare semantic values and preserve the underlying evidence. Stop on unexplained differences.
Record findings from each phase when it actually runs; an update pass does not establish that the
initial-install guide works, and an ad hoc combined cycle does not validate the written procedure.

An uninstall that preserves reviewer state is not a clean-profile installation. Use a new
disposable target without a receipt to exercise first-time brief creation and operator
acceptance, while keeping the existing targets' state and unrelated host settings. Do not purge
real reviewer state merely to simulate a clean machine. If the operator explicitly requests a
full uninstall including all plugin configuration and history, follow
[AGENT_UNINSTALL.md C3a](../AGENT_UNINSTALL.md#c3a--full-uninstall-remove-plugin-configuration-and-history),
verify the empty plugin baseline, then follow the initial-install guide. This tests first-time
plugin installation in an existing host account; it does not establish clean-account or
clean-machine coverage. Report these separately.

## 1. Record the environment and preserve state

Record native Linux versus WSL2, distribution, kernel, CPU count, Git, Python, host and selected
backend versions. Record the filesystem types for the checkout, target and `TMPDIR`, including
whether the temporary directory is actually NFS. Do not describe WSL2 as native Linux.

```bash
cat /etc/os-release
uname -sr
getconf _NPROCESSORS_ONLN
git --version
command -v python3
python3 -c 'import sys; print(sys.executable); print(sys.version); assert sys.version_info >= (3, 9)'
git rev-parse HEAD
git status --short
python3 tools/release_versions.py
python3 tools/release_cachebuster.py --print
```

Run these in the clean, pinned installer checkout. Substitute the chosen Python 3.9+ executable
throughout. Ubuntu's system Python 3.8 is too old; no particular conda environment is required.
Use a permanent interpreter for the Codex installer, since it saves that interpreter's absolute
path. A temporary test venv is suitable for pytest, not for the installed Codex runtime.

Record `codex --version` or `claude --version`, and versions of the selected reviewer executables.
Record executable paths without publishing account names or credentials. Save a private baseline
of relevant settings, plugin registrations, feature flags, receipts and unrelated plugin entries
before installation; compare after update and uninstall. Do not copy those files into the report.
Use repository-local Git identity in disposable fixtures if needed; do not rewrite global identity.

The earlier Linux findings involved Git 2.25, two CPUs, Python discovery and NFS temporary files.
Exercise those conditions where available. If Git 2.25 or NFS is absent, mark that coverage
**NOT RUN** and name the missing environment; newer Git/local storage does not prove it.

## 2. Shared automated and distribution checks

Run once per recorded environment. Install `requirements-dev.txt` into a test venv with the
chosen interpreter. Use fresh export directories in a permanent build area; placeholders below
must be replaced. Set `TMPDIR` to the operator's writable NFS test directory **before** starting
pytest when NFS is part of this acceptance. Confirm its filesystem with `findmnt -T` or `df -T`.
Do not silently fall back to local temporary storage after an NFS failure.

From the clean pinned checkout:

```bash
python3 tools/check_split_layering.py
python3 tools/release_versions.py
python3 tools/scan_release.py
python3 tools/release_export.py --target <EXPORT_A> --archive <ARCHIVE_A>
python3 tools/release_export.py --target <EXPORT_B> --archive <ARCHIVE_B>
cmp <ARCHIVE_A> <ARCHIVE_B>
sha256sum <ARCHIVE_A>
python3 tools/scan_release.py --path <EXPORT_A> --no-commit-check
```

Run the following **from `<EXPORT_A>`**, using the test venv's Python instead of `python3`.
This exercises the distribution without running both full suites a second time. Retain its
association with the source SHA and archive checksum; an export has no `.git` directory.

```bash
python3 -m pytest -m smoke -q -p no:cacheprovider -rs
python3 -m pytest -p no:cacheprovider -rs
(cd plugins/reviewer-agent-codex && python3 -m pytest -p no:cacheprovider -rs)
```

Record elapsed time and final exit status for each command, and every skip with its reason.
The install smoke subset should finish in **under 60 seconds**; a slower result needs diagnosis,
not an invented pass. Both full suites must finish successfully. Use background execution when
necessary and collect the final result; an active session is not a successful test.

Install the host package from this permanent export to test the artifact. The install guide's
`<INSTALLER_REPO>` may refer to that export. For an existing Codex source link, follow
[source-link migration](../plugins/reviewer-agent-codex/README.md#changing-the-existing-source-directory)
before installation; the installer will not overwrite a link to the previous checkout. Do not delete it while an installation points there.
The root suite does not collect the nested Codex suite. Existing regression tests cover launcher
failure, large stages, Python discovery, Git compatibility and filesystem handling; inspect any
failure or unexpected skip instead of creating more tests merely to repeat their assertions.

For Pi coverage, also run in `pi-extensions/git-read-only`:

```bash
npm ci
node test/run.mjs
npm audit --package-lock-only --audit-level=high
```

For authorized live backend checks, list scenarios with `python3 tools/release_matrix.py --list`.
Run only the selected scenarios, saving a report, for example:

```bash
python3 tools/release_matrix.py --scenario claude-codex --keep --report <MATRIX_REPORT>
```

Other live scenarios are `claude-pi`, `claude-claude`, and `codex-native`. Install only the
operator's selected backend executables; the Claude backend uses the existing Claude CLI.
A stub result is not live acceptance.
Do not use `--all` here: its `distribution` scenario would repeat the full suites already run.
Report unexercised backends explicitly. Read-only command/help or stub checks do not spend
provider quota, but the listed live scenarios do. Save the workspace path printed by `--keep`;
the default deletes the raw evidence. Keep provider transcripts private and report sanitized
outcomes, including actual Stop verdicts. The current matrix's unfinished-Stop check can pass on
a fail-open diagnostic containing `stopContinue`: require a real blocking decision, a nonempty
directive and a successful reviewer outcome before counting that case as accepted.

The matrix creates acceptance receipts only in its isolated synthetic fixture state. That test
setup does not authorize enabling any real repository or replace the operator's exact
`acceptRisk` invocation at the host checkpoint below.

## 3. Codex agent checks

Read the [Codex guide](../plugins/reviewer-agent-codex/README.md), especially installation,
configuration, update/uninstall and post-install verification. Use its local installer:

```bash
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

Record the installed plugin path, version and source/artifact comparison. The fresh MCP server
must advertise the candidate version and exactly `review_commit`, `stop_hook`, `interrupt_stop`.
**Never invoke `stop_hook` or `interrupt_stop` manually**: they are host lifecycle tools. Do not
parse Codex rollout JSONL; use documented hook payloads, CLI events and retained review evidence.

**Operator checkpoint:** open interactive Codex CLI, run `/hooks` and trust the installed hooks.
Enable commit review and stop supervision in the disposable target using the bundled skills;
only the operator may personally invoke their required `acceptRisk` forms. Restart into a fresh
session. For a desktop test, fully restart the GUI too. Confirm flags and receipts persist.

| ID | Test and required observation |
| --- | --- |
| CX1 | **Real commit gate:** in the target, stage a tiny function with a deterministic failing test and ask the agent to commit. Observe shell-commit redirection into `review_commit`, rejection and unchanged HEAD. Correct the function, demonstrate the test passes, retry, and verify the approved commit contains exactly the reviewed stage. |
| CX2 | **Escape during commit review (operator):** cancel while checking the commit. HEAD stays unchanged and the index stays staged. A subsequent gated retry can approve and commit. |
| CX3 | **Automatic Stop:** a completed answer receives an actual approval; unfinished authorized work receives a continuation, resumes and later receives approval. Missing hooks, fail-open errors or exhausted budgets are not approvals. Use the CLI check below for the incomplete/corrected sequence. |
| CX4 | **Removed delay:** review starts without the former fixed 60-second wait. In isolated test configuration, legacy `stopContinueDelaySecs` and `REVIEWER_STOPCONTINUE_DELAY_SECS` values, even `300`, have no effect. Legacy `stop-continue delay` commands report removal without writing settings. Allow actual backend startup time; “immediate” does not mean zero latency. |
| CX5 | **Escape during Stop review (operator):** an active review cancels, with no late continuation from it. Interrupt ordinary work before Stop too: that interruption must not launch a completion review. Submitting a reply is a documented Codex limitation and is **not** a supported substitute for Escape. |
| CX6 | **Controls/config:** show, set, show and clear model/effort, reviewer-session audit limit and history controls using documented forms. Verify invalid values preserve configuration, environment precedence, global overrides versus repository flags, and that history clear affects only the disposable target. Inventory all five active JSON keys against the reference; use existing tests for exhaustive numeric boundaries. |
| CX7 | **Stop decisions:** completed question, completed compaction handover, unfinished authorized task, and work with a background server. Completed work may stop; unfinished work continues. A legitimately awaited operation may return `STOP_WAIT`. A listening server alone must not disable supervision. Record actual prompt/verdict on a misjudgment. |
| CX8 | **Update/restart:** rerun the supported installer, verify installed bytes, restart and repeat one actual gated commit and automatic Stop. Feature state, receipts and unrelated plugins/settings survive. Do not mutate the candidate's cachebuster to make installation pass. |
| CX9 | **Uninstall/reinstall:** follow the Codex section of `AGENT_UNINSTALL.md` directly with no review/commit active. Preserve evidence and verify removal, unrelated configuration and retained state before reinstalling. Complete the fresh-host removal checkpoint, then follow `INSTALL_WITH_AGENT.md` from step 1, trust/restart and verify a real gate and automatic Stop. Report the direct-only namespace entry left in place for reinstall. Do not use `--purge-state` or a temporary combined wrapper. |

CLI completion check, from the installer/export root, after enablement and hook trust:

```bash
python3 plugins/reviewer-agent-codex/scripts/check_cli_stop.py --run \
  --cwd <CODEX_TARGET> --plugin-root <INSTALLED_CODEX_PLUGIN>
```

This consumes model usage. Save its event artifacts and exit status. It verifies automatic Stop,
rejection, correction and approval; it does not verify Escape, desktop painting or all commit paths.
If a Linux GUI is unavailable, mark GUI checks **N/A** and perform the applicable interactive CLI
checks. Do not claim Linux CLI results establish desktop behavior.

## 4. Claude Code agent checks

Read [AGENT_INSTALL.md](../AGENT_INSTALL.md), [AGENT_UPDATE.md](../AGENT_UPDATE.md),
[AGENT_UNINSTALL.md](../AGENT_UNINSTALL.md), and the
[command/config reference](../README.md#claude-code-commands). Install the permanent export as
a Directory marketplace, as described in `INSTALL_WITH_AGENT.md`. Use the operator's chosen
`codex`, `pi` or `claude` reviewer backend; install no unused alternative provider.

Record `claude plugin list`, the installed entry's version and source path, and compare the
installed hook files to the candidate export. A matching version string alone is insufficient.
For a GitHub-source update, record `gitCommitSha` too; an export has no source Git commit metadata.
Same-label `claude plugin update` may copy nothing: use the documented reinstall procedure if
needed, and do not treat its “already at the latest version” message as byte verification.

**Operator checkpoint:** restart Claude Code in its disposable target, read the disclaimer,
personally invoke the exact required `acceptRisk` forms, and enable the requested features.
Confirm the selected backend reads back correctly and the gate is actually active.

| ID | Test and required observation |
| --- | --- |
| CL1 | **Real commit gate:** stage the same kind of tiny deterministic defect and failing test as CX1. A real commit is rejected with HEAD unchanged; fix, pass the test and obtain an approved commit. Check the recorded reviewer used the selected backend. |
| CL2 | **Escape during commit review (operator):** cancel an active check, verify no commit and the staged edits remain, then retry successfully through the gate. |
| CL3 | **Stop and spinner:** unfinished authorized work continues; a completed task/question and completed compaction handover may stop. Verify the label `stopContinue supervisor (reply to skip the review)` and actual verdict evidence. Test with a background server and distinguish it from legitimately awaited work. |
| CL4 | **Reading window (operator):** confirm the default 60-second Claude wait. Reply during that window: review is skipped without a provider call. Then let a review start and reply during it: cancellation must occur without a late directive. Test Escape in each phase too. Timestamp input, review start and cancellation; do not guess from spinner disappearance. |
| CL5 | **Delay controls:** show/set/show/clear `/reviewer-agent:stopContinue delay`; exercise `0` and `300` configuration and invalid input without waiting five minutes unnecessarily. Verify environment > command override > JSON > default. Restore the original effective delay afterward. |
| CL6 | **Model/timeout/session controls:** show/set/show/clear the documented command forms for the selected backend. Specifically, clear a command timeout with a different JSON or environment timeout still in force: report its effective value and source. Reject a timeout at/above the installed hook ceiling, invalid model/effort/limit input, and preserve unrelated settings. Record global versus project scope. |
| CL7 | **Directives/history:** add/list/remove a harmless fixture directive; output starts `reviewerDirective SUCCEEDED` (invalid action: `reviewerDirective FAILED:`), even if the host titles it a blocked prompt. Verify it reaches reviewer context. Clear history only for the target and check another project's records survive. |
| CL8 | **Question rerouting:** exercise an actual interactive `AskUserQuestion` while supervision is enabled, observe the reroute and resulting supervision. A unit test or direct hook invocation is insufficient. Record a reproducible failure if supervision edits a satisfactory answer or revives finished work. |
| CL9 | **Watchdog/restart:** show status, arm, inspect the host's cron list, and check pause after approval/interrupt and resumption after a new request. Restart and verify persisted state with no duplicate cron. A cron may survive; if absent, `SessionStart` does not immediately restore it. Re-arming depends on a tick older than two configured intervals (14 minutes at default 7); use a short supported test interval or mark observation pending, rather than waiting silently or claiming immediate recovery. Restore the prior setting. |
| CL10 | **Linux launch path:** verify hooks find Python 3.9+ and the chosen backend from the host's actual launch environment, not just a test shell. Review the suite's launcher/binary failure results. Do not break the user's global PATH or remove an executable to simulate failure. A launcher error must not be counted as an approved commit. |
| CL11 | **Uninstall/reinstall:** follow `AGENT_UNINSTALL.md` with no audit in progress. Compare unrelated hooks/plugins/settings and retained data with the baseline, then reinstall, restart and verify a real gate and automatic Stop. Avoid optional data purges and restore the operator's backend/model/delay/watchdog choices. |

For the main Stop decision checks, temporarily set Claude's delay to `0` to save time; restore it
for CL4 and restore the original effective setting at the end. Do not silently alter real work
authorization or authorize a destructive action merely to test the supervisor.

## 5. Known limits and final disposition

- Codex currently requires **Escape** to interrupt a review. A submitted reply was delivered
  only after review completion in desktop acceptance. This limitation is documented and accepted;
  it is not a successful reply-cancellation test.
- Removing Codex's fixed wait fixes the confirmed extra minute. Intermittent missing/disappearing
  desktop text remains unresolved; one clean run or Linux CLI success cannot establish a fix.
- Stop supervision may misjudge scope or return prose instead of its required approval token.
  Record the actual failure; do not relabel it as approval or change the parser in this test run.
- GitHub Actions was blocked before jobs started by the account's payment/spending-limit state
  during release preparation. Report current CI separately; a billing failure is not a test failure,
  and Linux results do not automatically clear that owner action.
- A native Linux pass does not verify WSL2 or unauthenticated public installation. The repository
  is still private during preparation. Publication, visibility changes and public-install checks
  remain separate operator decisions and steps.

At the end, stop only fixture processes you started, collect final shell-session outcomes and
restore modified settings. Leave the permanent install source in place. Do not end with a test
quietly still running or delete another agent's target or evidence.

## Result template

Save a sanitized report outside the release checkout; provide its path to the operator. Keep raw
provider transcripts/config backups private. Use **PASS / FAIL / BLOCKED / NOT RUN / N/A** for
each shared check and each applicable host row. Name the reason for every non-pass.

```text
Host under test: Codex | Claude Code
Candidate: full SHA; 1.4.3; Codex build suffix
Artifact: SHA-256; export path; installed version and byte-comparison result
Environment: native Linux/WSL2; OS/kernel; CPU; Git; Python executable/version
Filesystems: checkout; target; TMPDIR; NFS confirmed yes/no
Developer host version; reviewer backend/model/effort and executable version:
Shared report reused, if any: path and matching environment/SHA

Check ID | status | command/action | exit status, duration | evidence | issue/next action
Shared: smoke, root suite, Codex suite, skips, layering, versions, source/export scan,
        reproducible archives, Git 2.25, NFS, Pi checks and each live matrix scenario.
Host: CX1–CX9 or CL1–CL11; identify operator-confirmed observations explicitly.

Remaining operator actions: each action, why it requires the operator, expected result
Settings restored; unrelated state preserved; installed source retained:
Fixture processes and shell sessions: all ended, or explicit identifier/owner and reason
Disposition: ready for release decision | blocked; list defects and untested requirements
```

Do not give a blanket pass while mandatory checks are missing. Report newly found defects and
known limitations separately so the maintainer can make the release decision with the evidence.
