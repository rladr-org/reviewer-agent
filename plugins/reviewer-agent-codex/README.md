# reviewer-agent-codex


> **Nothing here is supported, and nothing is guaranteed to work.** This is a personal project published as-is, with no undertaking that it is maintained or monitored, that any issue or security report will be read or acted upon, or that any fix will ever appear. It has been run in one person's private installation on macOS Tahoe and nowhere else. Use it entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md).

> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

A Codex-native autonomous reviewer with two independent, per-repository capabilities:

1. **Commit gate:** intercepts `git commit` and immediately redirects Codex to one blocking
   `review_commit` MCP call. The developer model is suspended until an isolated reviewer audits
   the staged index; an approved index is committed by the tool and every other outcome fails
   closed. Escape cancels the call and its nested reviewer process.
2. **stopContinue:** automatically reviews attempted stops and directs Codex to continue when
   authorized work remains. Its dedicated supervisor checks operator prompts, the current Codex
   plan/task list, and the proposed final message. It does not receive `reviewerAgent.md` or perform
   a general repository audit. Codex invokes the supervisor automatically at Stop. A rejected
   response can already be visible when Codex resumes work. This is continuation
   supervision, not a guarantee that every displayed response has been approved.

Both features are off by default.

> **`stopContinue` is autonomous delegation, not merely a reminder.** Its model output is fed back
> to the developer agent as a next-step directive. The developer agent may act on that output with
> its own tools, credentials and permissions, potentially without another human confirmation. The
> supervisor may misunderstand the operator's scope or incorrectly conclude that an action was
> already authorized. Limit external and destructive permissions before enabling it; see
> [`DISCLAIMER.md`](../../DISCLAIMER.md).

## Requirements

- An installed and authenticated Codex CLI with lifecycle hooks enabled. The GUI may be used as
  the developer host; CLI stop supervision has also been exercised in the limited checks below.
  The CLI is required for hook review/trust because `/hooks` is not a GUI slash command.
- Python 3.9 or newer; runtime code uses only the standard library.
- An authenticated `codex` executable available on `PATH`, or `REVIEWER_CODEX_BIN`.

## Platform support

The Codex GUI on macOS is tested. On macOS, Codex CLI 0.154.0 has also been exercised as the active
host for stop supervision through automatic Stop rejection and retry. These smoke checks do not establish coverage of every
interactive terminal, key binding, or the CLI commit-gate flow. See
[CLI completion checks](#cli-completion-checks) for the repeatable procedure.
Native Linux and Windows through WSL2 remain untested live-host beta platforms. Unit tests run
in Linux and macOS CI; live Linux/WSL2 host coverage is still absent. Native
Windows will not work because the reviewer relies on POSIX process and signal APIs. WSL1
will not work because [Codex's Linux sandbox](https://learn.chatgpt.com/docs/windows/wsl) uses
`bubblewrap`, which requires WSL2.

### Native Linux (untested)

Install Git, `curl`, and Python 3.9 or newer using the distribution's package manager. If Codex is
not already installed and authenticated, use the
[official Linux installer](https://learn.chatgpt.com/docs/windows/wsl) and verify both executables:

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
codex --version
python3 -c 'import sys; assert sys.version_info >= (3, 9); print(sys.version)'
```

Then use [Shared repository install](#shared-repository-install) below. Run Codex, the checkout
installer, and repositories protected by the plugin in the same Linux environment.

### Windows through WSL2 (untested)

Install WSL2 from an elevated PowerShell window and restart Windows if prompted:

```powershell
wsl --install
wsl --list --verbose
```

The selected distribution's `VERSION` column must show `2`. If it shows `1`, back up important
distribution data and convert it before continuing, substituting its listed name:

```powershell
wsl --set-version <distribution-name> 2
```

Open the verified WSL2 distribution and confirm its environment and kernel generation:

```bash
echo "$WSL_DISTRO_NAME"
uname -r | grep -qi 'WSL2' || { echo 'WSL2 not detected'; exit 1; }
```

Run Codex and the installer inside WSL2. Do not invoke a Windows Codex installation from WSL or run
this plugin from PowerShell, CMD, Git Bash, or WSL1. Keep the checkout and protected repositories
in the WSL Linux filesystem, such as `~/code`, rather than `/mnt/c`, then follow the native Linux
and shared-install directions above.

Please report Linux or WSL2 failures at
[GitHub Issues](https://github.com/rladr-org/reviewer-agent/issues). Include the distribution and
version, native Linux or WSL2, Codex and Python versions, the failed command or hook, and the full
sanitized error output.

## Install

### Agent-assisted installation

For the minimal-human route covering clone, verification, installation, project brief creation,
feature toggles, restart, and hook trust, start Codex in the repository to protect and give it the
prompt in [`INSTALL_WITH_AGENT.md`](../../INSTALL_WITH_AGENT.md). The agent runs the same
installer below and stops only at the human-only trust and restart checkpoint. GUI users complete
hook trust from the CLI as described below.

### Shared repository install

Clone the combined reviewer-agent repository into a permanent user-owned directory outside the
repository to protect. The installer creates `~/plugins/reviewer-agent-codex` as a symlink to
this checkout's package directory. Keep the checkout for source fallback and future updates;
a temporary checkout can leave an existing cache working while breaking the next update.

Use an available Python 3.9+ interpreter. From the combined repository root, prepare a test
environment using `requirements-dev.txt` and run the nested suite before installing:

```bash
git clone https://github.com/rladr-org/reviewer-agent.git
cd reviewer-agent
python3 -m venv /absolute/path/to/new-test-venv
/absolute/path/to/new-test-venv/bin/python -m pip install -r requirements-dev.txt
cd plugins/reviewer-agent-codex && /absolute/path/to/new-test-venv/bin/python -m pytest -p no:cacheprovider
```

Substitute your chosen environment path; no named conda environment is required. Test dependencies
are not runtime dependencies. Run the installer from the combined repository root after the tests
finish successfully:

```bash
cd /absolute/path/to/reviewer-agent
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

If cloning the configured source fails with an authentication error, verify access and use the
operator's authenticated Git transport, whether the source is canonical, a fork, or a mirror.
Keep the working transport, credential helper, and any SSH key-selection configuration available
to `git pull --ff-only` during later updates. Do not put credentials into URLs or logs.
The repository also exposes a Codex team-marketplace manifest and can be registered for
discovery with:

```bash
codex plugin marketplace add rladr-org/reviewer-agent
```

Registration alone is not the whole installation. A bare `codex plugin add` cannot run
this plugin's host-configuration step, so use the checkout installer above to configure the
agent-invoked commit review as a blocking direct call.

The marketplace layout follows Codex's official
[plugin distribution documentation](https://developers.openai.com/plugins/build/plugins/): the
repository manifest is `.agents/plugins/marketplace.json`, and its local source resolves to this
directory.

### Existing checkout or local development

If the current directory is already `plugins/reviewer-agent-codex`, run:

```bash
python3 scripts/install_local.py
```

The installer exposes this checkout through Codex's personal marketplace and runs
`codex plugin add`. It preserves other personal plugins and refuses to replace an unrelated source
path. It is designed to be rerunnable. It atomically parks and moves back every prior immutable version
directory around `codex plugin add`, preserving each live directory inode rather than copying it
after deletion; hooks and long-lived MCP servers already trusted by a live thread keep working,
while new threads load the newly installed version. If a successful install replaces the same
version name, the new directory remains canonical and the displaced live inode remains in its
dot-prefixed sibling parking directory. It also uses Codex app-server's atomic configuration RPC
to merge `mcp__reviewer_agent` into
`features.code_mode.direct_only_tool_namespaces`, preserving other configured namespaces. This
makes the agent's `review_commit` call a blocking direct call instead of a yieldable code-mode
operation. Completion review is invoked automatically by the Stop lifecycle hook. Use this
installer for updates rather than invoking `codex plugin add` directly.

Before writing configuration, the installer checks that `python3` on its PATH runs Python 3.9+.
Launch Codex with a PATH that resolves an appropriate `python3` too; a check in the installer
shell does not establish the environment of an already running GUI. PreToolUse converts input,
import, and gate errors to an explicit deny. Its shell launcher exits with blocking status 2
if Python or the hook script cannot run. These responses do not cover an untrusted/missing hook
registration or a host that kills the whole launcher on timeout; verify actual host interception.

Each command-based lifecycle hook also carries its own cache-eviction fallback. It uses the immutable
`PLUGIN_ROOT` selected when the thread started while that directory exists. If a direct
`codex plugin add` removes that version anyway, the command resolves the same hook through the
newly installed immutable cache, then through the permanent
`~/plugins/reviewer-agent-codex` source link as a last resort. This protects command hooks such as
PreToolUse from missing-script failures during cache replacement. Stop and Interrupt use the
connected MCP server directly.

The MCP backend snapshots its process guard and process-tree helper when it starts. Later
review launches use that retained code, so temporarily parking or removing the original cache
does not prevent a running server from launching a review. Cancellation, timeouts, and cleanup
when the MCP parent exits apply throughout installation.

Then open a terminal and run `codex` to start an interactive CLI session. Run `/hooks` there,
inspect the `reviewer-agent-codex` lifecycle hooks, and trust any entries requiring approval.
`/hooks` is not a Codex GUI command. Exit the CLI, fully quit and restart the GUI, then start a new
GUI chat. Codex loads newly
installed plugin skills and hooks at that session boundary. CLI users instead start a fresh
interactive CLI session. The CLI smoke coverage and its limits are described above.

To update, pull the combined repository and re-run the same installer:

```bash
git pull --ff-only
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

The `reviewer-agent` and `stop-continue` flags are stored per repository and survive Codex restarts.
You only need to enable them once unless you explicitly turn them off or delete the state directory.
Upgrades also preserve risk-acceptance receipts; a current receipt does not require accepting again.

Project identity uses the canonical Git common directory, resolving relative output against the
repository root. This keeps linked worktrees together without the newer `--path-format` option.
If an older installation ran with Git 2.25.1, its incorrect keys may have mixed records from
unrelated repositories. This update does not migrate or delete those ambiguous records. Back up
the state directory, inspect/quarantine the affected records, and deliberately reconfigure each
repository; do not copy old authorization tickets or stop permits into the corrected keys.

Copy `reviewerAgent.md.template` to the target repository as `reviewerAgent.md` if no brief exists,
and replace its project-guidance placeholder with the project's rules and test commands before
invoking `$reviewer-agent-codex:reviewer-agent on`. Enablement rejects an empty brief or the known
untouched placeholder. Preserve customized briefs. In copies of older templates, remove the
human-facing copy/enable preamble manually; the composer reads the complete file as instructions.

Starting or resuming a thread never launches a reviewer model. Reviewer sessions are initialized
only when an enabled commit gate or stop supervisor performs an actual review.

The first stop check creates its dedicated stop-supervisor thread and obtains the first ruling in
that same MCP call and model turn. There is no separate warm-up or discarded briefing response.

The first commit review likewise combines the standing/project brief with the staged audit in the
reviewer thread's first ruling turn. There is no separate commit briefing call. Later commit reviews
in the same developer session resume that reviewer thread without re-sending the project brief;
they receive the staged-set delta, current operator context, and audit request.

Every commit-review turn also prepends a compact, current standing reminder. It takes precedence
over stale instructions retained by a long-lived reviewer thread, restates the fitness-for-use and
scope standards, names the project brief when one exists, distinguishes staged content from the
working tree, and explains the `CHANGED`/`ADDED`/`UNCHANGED`/`REMOVED` retry contract. Once a prior
verdict exists for that developer session, the reminder names its durable file and requires an
explicit disposition for every prior finding, including withdrawal of an incorrect finding.

The first commit turn also receives the full Claude-aligned audit brief: fitness for actual use,
source reasoning as evidence even when a path cannot run locally, whole-staged-set review, fresh
versus retry semantics, verification of staged claims, targeted reading, immediate execution of
relevant checks, artifact-free Python test flags, and an exhaustive required-fix verdict. Project
rules add to that harness brief and win where they are more specific.

Reviewer thread files are scoped to a fingerprint of the static prompt for that role. Calls with
the same prompt keep reusing the same reviewer, but installing changed commit instructions
automatically starts a fresh commit reviewer even when Codex resumes the same developer session ID.
The independent StopContinue prompt has its own fingerprint and thread. A fresh reviewer is not
shown an older review file as though it were that reviewer's own verdict.

Commit commands must name their repository explicitly as `git -C /absolute/repository commit ...`.
Codex hook events do not expose a shell tool's separate `workdir`, so a plain `git commit` is denied
without minting a token and returns the exact absolute-target retry instruction. The unambiguous
retry returns a token and the exact `cwd` argument for `review_commit`.

Accepted commit options are `-m`/`--message`, `-s`/`--signoff`, and `-q`/`--quiet`.
The ticket preserves sign-off and quiet flags; the MCP call supplies the message. Other options,
pathspecs, alternate Git configuration, and compound shell commands are rejected explicitly before
a ticket is created. Stage files first and run other shell commands separately.

The gate records the staged set's `git write-tree` hash before review and compares it again
immediately before committing. If staging changed, a fresh review is required. On approval it
runs ordinary `git commit` in the real repository, with the configured hooks and signing. Hooks
may format or stage content, and their changes are committed normally. No private index or locked
publication transaction is used. The hash check and commit are separate operations, so concurrent
writers should use separate worktrees. Git hooks and signing have a 300-second time limit.

If Git fails or is interrupted and HEAD changed, the tool returns `publication_unknown`: the
commit may have published, or a hook or another writer may have moved HEAD. An unreadable HEAD
also leaves the outcome uncertain. Inspect `git log` and
`git status` before retrying; the commit may already have happened. Cancellation stops a running
Git process and its known children, but cannot undo a commit Git has already published. Failed
hooks may leave working files or the real index changed; those changes are not rolled back.

Git commands run directly and synchronously; only the nested reviewer uses a lifecycle guard.
Commit output is captured in temporary files, so a repository hook's background helper
cannot keep the commit waiting for output pipes to close. Hooks remain responsible for helpers
they detach after exiting; the plugin does not launch those helpers.

Every completed commit audit is written to two durable locations under the state directory. A
stable per-project, per-developer-session file holds the latest verdict for retry prompts; the MCP
result prints that path before the inline verdict so it remains visible when Codex collapses or
truncates long tool output. A separate per-project history directory receives a unique,
non-overwriting entry for every completed verdict, including approvals and rejections, across
developer sessions.

Timeouts, cancellations and operational failures also reach the history, as labelled `OUTCOME:`
entries recording that a commit was blocked. A review that finished but could not be applied --
because the staged set changed underneath it, or because no reviewer thread was captured -- is
recorded as `OUTCOME: DISCARDED` with the unused verdict preserved in the entry. None of these
write the latest-verdict file, which the next reviewer is shown as its own previous findings.

The per-project history retains the newest 100 ENTRIES by default and prunes the oldest first;
verdicts and `OUTCOME:` records share that one cap. Set `reviewHistoryKeep` in the persistent JSON
configuration, or set `REVIEWER_CODEX_REVIEW_HISTORY_KEEP`, to choose a different positive
retention count. The setting is read whenever an entry is written, so changing it does not require
a plugin reinstall. Raising it does not restore entries that were already pruned.

`$reviewer-agent-codex:reviewer-history status` reports what one repository has retained and where;
`$reviewer-agent-codex:reviewer-history clear` deletes that repository's retained reviews and saved
rulings. Verdicts quote the staged diffs they judged, so this is how an operator removes that
plaintext record without uninstalling the plugin. Other repositories are unaffected, and no
reviewer session is retired.

The reviewer may append a `SUGGESTIONS:` block to either an approval or rejection. The MCP result
labels those observations as non-blocking and, on rejection, separates them from the required fixes
that must be addressed before retrying. Ambiguous or repeated suggestion markers fail closed rather
than silently demoting a required fix.

To prepare the marketplace entry without invoking Codex, use:

```bash
python3 scripts/install_local.py --prepare-only
```

## Update, disable and uninstall

**Update** an existing installation by pulling the checkout and reinstalling; the version's
`+codex.<timestamp>` build metadata is what makes Codex notice the change rather than serving
its cache.

```bash
git pull
python3 tools/release_cachebuster.py          # from the repository root
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

Afterwards, open an interactive `codex` CLI session, run `/hooks`, and trust any plugin hooks
requiring approval.
Then exit the CLI, fully quit and restart the GUI, and start a new GUI chat. `/hooks` is not a Codex
GUI command. CLI users instead start a fresh interactive CLI session.
Then complete the [post-install verification](#post-install-verification); a successful install
does not prove that an existing task has loaded the new server or hook definitions.

**Disable without uninstalling**, per repository:

```text
$reviewer-agent-codex:reviewer-agent off
$reviewer-agent-codex:stop-continue off
```

**Uninstall**, which removes this plugin's configuration and nothing else — other plugins,
marketplaces, MCP servers and settings are left alone:

```bash
python3 plugins/reviewer-agent-codex/scripts/uninstall_local.py
```

It asks Codex to remove the installed plugin, deletes its cache and its entry in the personal
marketplace, and removes the `~/plugins/reviewer-agent-codex` symlink pointing at this checkout.
It is designed to tolerate a repeat run or a half-finished prior install. Two things it does not do:

- **Retained verdicts and reviewer state survive.** They are your data, not installer output.
  Add `--purge-state` to delete `~/.codex/reviewer-agent-codex/` as well, or use
  `$reviewer-agent-codex:reviewer-history clear` to purge one repository's review record while
  keeping the plugin.
- **One config edit is left to you.** Installation added `mcp__reviewer_agent` to
  `features.code_mode.direct_only_tool_namespaces` through Codex's own config RPC. Removing one
  entry from a shared array is left manual to avoid rewriting a running host's shared setting, so the uninstaller
  prints the exact edit instead. Leave the other namespaces alone.

## Enable in a repository

Write `reviewerAgent.md` at the repository root, starting from `reviewerAgent.md.template`. Then
invoke the bundled skills:

```text
$reviewer-agent-codex:reviewer-agent on
```

The first invocation does not enable anything. Read [`DISCLAIMER.md`](DISCLAIMER.md), then invoke:

```text
$reviewer-agent-codex:reviewer-agent on acceptRisk
$reviewer-agent-codex:stop-continue on
```

One current receipt covers both features for this repository. If stopContinue is the first feature
enabled, its plain `on` response instead requires
`$reviewer-agent-codex:stop-continue on acceptRisk`. A changed disclaimer invalidates the old
receipt and requires a new explicit acceptance.

Use `off` or `status` with either skill to change or inspect its state. State defaults to
`~/.codex/reviewer-agent-codex`; override it with `REVIEWER_CODEX_STATE_DIR`.

Exact namespaced toggle invocations are applied by the trusted `UserPromptSubmit` hook before the
model runs. The hook ends the toggle turn without making a model request, launching a shell command,
or requiring a separate filesystem approval. Only that trusted exact prompt can record
`acceptRisk`; the fallback CLI cannot. Enabling stopContinue exempts only that enabling turn from
stop supervision; subsequent turns are supervised.

## Reviewer model

Nested commit and stop reviews default to `gpt-5.6-luna` with `xhigh` reasoning. Show or change the
effective settings with the bundled skill:

```text
$reviewer-agent-codex:reviewer-model
$reviewer-agent-codex:reviewer-model gpt-5.6-sol max
$reviewer-agent-codex:reviewer-model effort high
$reviewer-agent-codex:reviewer-model clear
$reviewer-agent-codex:reviewer-model effort clear
```

The persistent JSON configuration is `~/.codex/reviewer-agent-codex/config.json` by default:

```json
{
  "model": "gpt-5.6-luna",
  "reasoningEffort": "xhigh",
  "commitReviewTimeoutSeconds": 3600,
  "reviewerSessionAuditLimit": 3,
  "reviewHistoryKeep": 100
}
```

Resolution order is `REVIEWER_CODEX_MODEL` or `REVIEWER_CODEX_EFFORT`, then a setting written by
the reviewer-model skill, then `config.json`, then the built-in defaults. Invalid model tokens or
effort values are skipped. Valid effort values are `none`, `minimal`, `low`, `medium`, `high`,
`xhigh`, `max`, and `ultra`.

Commit reviews default to a 3,600-second (one-hour) fail-closed backend limit. Set
`commitReviewTimeoutSeconds` in that same JSON file, or set
`REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS`, to choose an integer from 60 through 7,200 seconds. The
environment value wins over `config.json`; invalid or out-of-range values are skipped. The MCP
tool ceiling is 7,260 seconds so every backend limit has time to return its ruling and
cleanup result.

Model settings are read on every child invocation and apply to both fresh and resumed reviewer
threads. A model name or effort the installed Codex host does not accept will cause that child call
to fail; the commit gate remains fail-closed and stop supervision remains fail-open.

Every fresh and resumed reviewer invocation requests Codex's fast/priority service tier. The
built-in profile is therefore `gpt-5.6-luna` with `xhigh` reasoning and
`service_tier="priority"`; explicit reviewer-model controls still override the model or effort.

## Reviewer session recovery

Each commit reviewer handles three completed non-retry audits by default, then retires so the next
audit begins in a fresh session. Once an audit rejects, every following commit attempt is a retry
until one approves; those retries do not advance the counter. A first-pass rejection itself counts
as one completed audit. Show, rotate, or configure the nested session with:

```text
$reviewer-agent-codex:reviewer-session status
$reviewer-agent-codex:reviewer-session rotate
$reviewer-agent-codex:reviewer-session limit 5
$reviewer-agent-codex:reviewer-session limit off
$reviewer-agent-codex:reviewer-session limit clear
```

`reviewerSessionAuditLimit` in the JSON config supplies the default; `0` disables automatic
rotation. The command override takes precedence, and `clear` removes it. Rotation affects only the
commit reviewer for the current developer session; the separate stopContinue supervisor is
unchanged. The next commit attempt creates a fresh reviewer and sends the full standing and
project brief.

If a commit audit reaches its configured fail-closed limit, no verdict is saved and no commit is
created. The plugin compare-safely retires only the timed-out reviewer session, clears only that
reviewer's staged-index cursor, and tells the developer to retry the shell `git commit` once. That
retry starts a fresh reviewer instead of resuming the pathological thread. A reviewer concurrently
installed as a replacement is preserved. Manual rotation is not needed after this automatic
timeout recovery.

## stopContinue context

The stop supervisor has its own thread and uses the same canonical prompt as Claude Code. Each ruling receives only:

- captured operator prompts;
- the latest `update_plan` plan/task list, when the agent used that Codex tool;
- the proposed final assistant message; and
- plan or task documents explicitly named by that context, which the read-only supervisor may
  inspect when needed.

It does not receive the commit reviewer's project brief, inspect code or diffs, check Git state, or
run tests. Repository-wide correctness review remains exclusively the commit gate's responsibility.
Within that deliberately limited context, the supervisor treats the agent's own statement of
remaining work as evidence, requires already-authorized and unfinished plan work to continue, and
allows a human-decision stop only when the decision is genuinely open and the proposed final message
presents the decision, options, recommendation, and exact reply needed to unblock progress.

## Blocking calls and cancellation

The commit tool advertises the display title
`Commit review - when completed, results are available here`. Its result includes the reviewer
verdict, findings, and Git output. Codex controls how the tool title and result are displayed.

Commit attempts return immediately with a one-use token tied to the owning Codex session. Codex
then calls the named MCP tool once. At an attempted turn ending, the bundled Stop hook calls the reviewer server
directly through Codex's native `mcp_tool` lifecycle handler, showing `Checking completion` while
it runs. It consumes its review permit within that same hook invocation. Approval adds no routing
prompt or routine success warning, preserving the original final answer. An incomplete ruling
adds only the supervisor's actionable continuation. Errors remain fail-open with a warning.
The plugin cannot force the app's previous-message dropdown to remain expanded.

Native MCP hooks require an already-connected server and a host supporting `mcp_tool` handlers.
Completion supervision is invoked only by the Stop hook. The developer model remains suspended
while the hook review runs. The Interrupt handler cancels matching active reviews.
The installer marks the reviewer MCP
namespace direct-only in Codex code mode, so the call cannot yield into a JavaScript cell that
reactivates the developer model for polling or narration. The stdio server continues reading MCP
messages while the review runs; on `notifications/cancelled` (including Escape), it terminates the
nested `codex exec` tree and emits no late tool result. Each nested reviewer also runs behind a
lifecycle guard connected to its MCP server by a kernel pipe. An abrupt Codex restart or forced
MCP-server exit closes that pipe and terminates descendant process groups as well as the reviewer's
original group. Kernel child enumeration reaches tests or shell commands that created their own
sessions, so a detached audit cannot survive as an orphan or consume capacity after its caller is
gone.

When Escape cancels a stop review, the plugin records a one-use abort bypass scoped to the Git
repository and developer session. Codex may assign a new turn ID when it emits another Stop event
while ending the interrupted turn; the hook consumes a fresh bypass and lets that event finish
without starting a second stop review. The bypass expires after 30 seconds and any genuine operator
prompt clears it before that prompt runs. This is not completion approval and cannot bypass
supervision for a later task. Escape likewise cancels `review_commit` without committing.

The native Interrupt hook also cancels reviews on hosts that do not forward ordinary MCP
cancellation. Stop reviews match the canonical working directory, session ID, and interrupted
turn ID. Pending commit calls match the owning developer session recorded in their one-use
tickets: the commit target may be another worktree, and a ticket may have been issued in an
earlier turn. All pending commit calls from that session are cancelled; other developer sessions
are unaffected. The server registers ownership before starting the worker, fails closed when
ticket ownership cannot be read, and suppresses late results after cancellation. The handler
returns silently within a three-second hook budget. Cancellation during the audit prevents the
commit; once Git has started, publication may already have occurred and HEAD must be inspected.

One developer session reuses its commit-reviewer thread across attempts. After every completed
verdict the plugin records the staged path/blob set. A retry on the same HEAD tells that same
reviewer which paths are changed, unchanged, added, or removed and inlines bounded exact index
deltas (`git diff <old-blob> :<path>`). Other developer sessions receive independent reviewers.
An audit that times out is the exception: its failed turn cannot authorize a commit, so its exact
reviewer thread and cursor are retired and the next attempt is deliberately fresh.

## Codex-specific liveness

The plugin records a shell item at `PreToolUse` and clears it at the corresponding `PostToolUse`.
Codex can deliver that post event when a later `write_stdin` poll observes completion. Subagents are
tracked from `SubagentStart` through `SubagentStop`. If Codex proposes stopping while either remains
active, stopContinue directs it to wait and inspect the result. Before blocking on a shell marker,
the Stop hook waits 250 milliseconds and rechecks once; only a concurrently delivered
`PostToolUse` clear changes that immediate outcome.

Codex exposes no lifecycle API for enumerating a unified-exec session whose `session_id` was lost,
and either a shell completion event or a `SubagentStop` event can be missed. Sandboxed hooks cannot
safely use the host process table or collaboration registry to prove whether such a marker is still
live. A stop with shell and/or subagent markers therefore routes the exact `(kind, id)` set directly
through the same minimal, cancellable completion supervisor. The supervisor may approve shell markers only when
the proposed final states that the named work was inspected or polled and no process or
unified-exec session remains; for subagent markers it must state that the collaboration registry
was reconciled and no live subagent remains. Otherwise it directs the developer agent to perform
the missing polling or reconciliation after the blocking call returns. Approval or fail-open clears
only the exact adjudicated `(kind, id)` set. Cleanup is best-effort so a state I/O error cannot
defeat StopContinue's fail-open contract. New work that appears during the ruling prevents the stop.

A yielded unified-exec response containing `session_id` is still active. Code-mode orchestration
must poll that exact session with `write_stdin` until it returns completion; waiting only on the
surrounding JavaScript cell does not poll the underlying command and intentionally leaves its
liveness marker active.

The `PreToolUse` event for `update_plan` also records the latest structured plan for the owning
session. This uses the documented hook payload and never reads rollout JSONL.

Liveness markers expire after 24 hours by default. Set `REVIEWER_LIVENESS_TTL_SECONDS` to tune the
expiry. The stop nudge cap defaults to 10 and is controlled by `REVIEWER_STOP_MAX_NUDGES`. Its
counter spans hook-generated continuation turns even when Codex assigns each one a fresh host turn
ID; only a genuine operator `UserPromptSubmit` starts a new budget. This bounds restart-only or
otherwise unsatisfiable continuation loops without carrying the exhausted budget into the user's
next task.

## Backlog

- **Pi reviewer backend.** Port the existing hermetic, read-only Pi commit-review backend from the
  Claude Code implementation into the Codex-native blocking `review_commit` MCP flow. This includes
  explicit per-repository backend selection, Pi model/thinking controls, fresh and resumed Pi
  sessions, bounded exact staged-index deltas, durable verdicts, fail-closed behavior, and the same
  process-tree cancellation implementation as the Codex backend. Treat Pi-backed `stopContinue` as a
  separate milestone: it must use its own role-scoped Pi session, receive only the minimal stop
  context described above, never receive `reviewerAgent.md`, and remain blocking, cancellable, and
  isolated from the Pi commit reviewer. Until that second milestone lands, `stopContinue` remains
  Codex-backed even when Pi is selected for commit review.

## Intended safety model

- Commit review is fail-closed.
- Commit and stop reviews suspend the developer model in a single cancellable MCP tool call.
- The installer configures the reviewer MCP namespace as direct-only without enabling code mode or
  replacing other direct-only namespaces.
- Cancellation terminates the nested reviewer and never commits or grants a stop permit; a
  stopContinue Escape only creates a short-lived, one-use bypass for the host's repeated Stop.
- Stop supervision is fail-open and bounded.
- The commit reviewer and stop supervisor use separate Codex threads.
- The first commit ruling is the reviewer thread's first model turn; no separate commit briefing is
  run.
- The first stop ruling is the stop thread's first model turn; no separate stop briefing is run.
- Stop supervision never receives `reviewerAgent.md`.
- Fresh and resumed reviewer calls request the priority service tier.
- Host-side output capture and reviewer children use a stable temporary root, and resumed turns
  capture only Codex's final agent message rather than its formatted CLI transcript.
- Both reviewer roles use the resolved reviewer model and reasoning effort.
- Nested reviewer processes explicitly disable lifecycle hooks and plugin loading, preventing
  recursive supervision or exposure of reviewer MCP tools inside the reviewer itself.
- The plugin uses documented hook fields and does not parse Codex rollout files.

See `BEHAVIOR_SPEC.md` for the complete behavioral contract.

## Host versions it has been run against

The installer warns when Codex CLI is below 0.154.0, then continues. It refuses installation
when the hook command `python3` is missing, cannot run, or reports a version below Python 3.9.
Codex 0.154.0 is the exercised native-host baseline, including Stop and Interrupt hooks.
The independent Claude package's Codex reviewer-backend baseline is separate.
Reviewed commits use the installed Git's ordinary commit behavior, without private reference
transactions or a separate Git 2.48/files-reference-format requirement.
Active-host CLI stop-supervision coverage is described under [Platform support](#platform-support).

## License

Mozilla Public License 2.0 — see [`LICENSE`](LICENSE) in this directory, which carries the
unmodified license text and is the notice location for the source files in this package.

## Completion review in the desktop app and CLI

Codex invokes the supervisor automatically at its Stop event. UserPromptSubmit and ordinary tool
hooks retain request and work context without initiating completion review. Every eligible Stop,
including a completed question answer, is reviewed using the same shared prompt as Claude Code.

Approval adds no continuation prompt. Rejection directs further work, and Codex can display the
rejected answer and collapse earlier messages. The plugin cannot control that desktop behavior.
A completed question answer also emits Stop: this lifecycle event does not distinguish that from
abandoning an active assignment.

Codex and Claude Code read one [shared supervisor prompt](prompts/stop_supervisor.txt).
Application-specific context supplies the request, work evidence, and final response.
Fail-open limits and cancellation apply to completion review.

## Post-install verification

After installation or an upgrade, verify that the reviewer MCP server is connected and the host
supports `mcp_tool` lifecycle hooks. Check the loaded hook mappings: `Stop` calls
`reviewer-agent.stop_hook`, and `Interrupt` calls `reviewer-agent.interrupt_stop`. The server's
tool inventory should contain exactly `review_commit`, `stop_hook`, and `interrupt_stop`.
Only Codex lifecycle events invoke `stop_hook` and `interrupt_stop`.
Do not ask the agent to call them manually.

With stopContinue enabled for the test repository, exercise these cases through the actual host.
Use read-only tasks for completion checks and a harmless long-running operation for interruption.
These checks consume reviewer model usage. Record the reviewer verdict or documented lifecycle
events, together with the host/version tested:

| Case | Required evidence |
| --- | --- |
| Completed answer | Stop launches a review and the reviewer approves it. |
| Unfinished authorized work | Stop launches a review that requests continuation; work resumes, and a later Stop is approved after completion. |
| Interrupt ordinary work before Stop | The turn is interrupted without launching a completion review. |
| Interrupt an active completion review | The running review is cancelled, without a late continuation from that cancelled review. |

The [CLI completion check](#cli-completion-checks) below automates the unfinished-work and later
approval sequence; it does not cover either interruption case or desktop rendering. Record any
unperformed case as pending. Unit tests alone do not establish that the installed host ran a hook.

An absent continuation prompt does not prove approval: missing hooks, a disconnected server,
or a fail-open error can also let a turn finish. Approval adds no continuation message. When the
reviewer requests more work, Codex can show its continuation directive and collapse earlier
messages. The plugin cannot guarantee hidden feedback or keep that dropdown expanded.

## CLI completion checks

The unit suite uses controlled reviewers. This separate check consumes real model usage and
requires stopContinue already enabled and the installed hooks trusted:

```bash
python3 plugins/reviewer-agent-codex/scripts/check_cli_stop.py --run \
  --cwd /path/to/enabled-repository \
  --plugin-root /path/to/installed/reviewer-agent-codex
```

The check runs one read-only CLI session. It requires observed events in this order: incomplete
response, automatic Stop request, rejection, corrected response, another Stop request, approval.
Missing hooks, manual lifecycle calls, reviews outside Stop, fail-open results, and exhausted
budgets cannot pass. Saved configuration, enablement receipts, and installed files are unchanged.
The default timeout is 180 seconds (`--timeout` accepts 10–900).
The printed artifact directory records documented CLI and MCP events, not internal rollout files.
