# reviewer-agent-codex


> **Nothing here is supported, and nothing is guaranteed to work.** This is a personal project published as-is, with no undertaking that it is maintained or monitored, that any issue or security report will be read or acted upon, or that any fix will ever appear. It has daily private use on macOS Tahoe; an Ubuntu installation produced issue reports, and clean-host acceptance of the current fixes remains pending. Use it entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md).

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

Commit reviewers must report `REVIEW_FAILED:` when they cannot inspect the staged input.
That operational failure blocks publication without replacing the previous verdict or advancing
the review cursor. The gate also blocks direct admissions of uninspected staged files in an
otherwise `APPROVED` response, including inside `SUGGESTIONS:`. This prose check covers explicit
failure reports; it is not proof that every other approval performed a complete audit. Optional
tests that cannot run after source inspection remain nonblocking suggestions.

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
The Ubuntu 1.4.3 candidate passed automated suites and actual CLI commit-review,
Stop-supervision and uninstall/reinstall checks. Clean-profile acceptance remains pending.
Windows through WSL2 is untested. Unit tests run in Linux and
macOS CI, including Git 2.25 on Ubuntu. Native
Windows will not work because the reviewer relies on POSIX process and signal APIs. WSL1
will not work because [Codex's Linux sandbox](https://learn.chatgpt.com/docs/windows/wsl) uses
`bubblewrap`, which requires WSL2.

### Native Linux (clean-profile acceptance pending)

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

After installation, hook trust and host restart are verified, follow the shared
[settings walkthrough](../../INSTALL_WITH_AGENT.md#2-resolve-choices-once). Confirm commit-review
settings, then immediately present its enablement screen and resolve that choice before asking
about Stop settings. Follow the same two-screen sequence for Stop, then confirm rotation/history.
Do not ask feature settings questions before installation or carry a premature answer forward as
confirmation. If the walkthrough started too early, finish the missing prerequisites, inspect the
loaded configuration, and ask again at the documented settings checkpoint.

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

Before writing configuration, the installer selects a working Python 3.9+ interpreter and checks
that the MCP server initializes with it. It prefers the interpreter running the installer, then
checks `python3` and versioned Python commands on PATH. It saves the absolute executable path in
`~/.codex/reviewer-agent-codex/python-path`; hooks and the MCP server both use this selection,
including when Codex starts with a different PATH. No system Python or shell configuration is
changed. You can run the installer with a specific interpreter, such as `python3.9`, to select it.
Keep that interpreter installed; if it is removed or moved, rerun the installer with a compatible
Python. Uninstall removes this selection. Direct marketplace installs without the local installer
still require a compatible `python3` on Codex's PATH. PreToolUse converts input,
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

For terminal use, start Codex in the root of the repository you want to work in. If the current
Codex session is already in that repository, exit and restart it there with `codex`; otherwise
exit, change to the intended repository root in your terminal, and run `codex`. Then run `/hooks`,
inspect the `reviewer-agent-codex` lifecycle hooks, and trust any requiring approval. Tell the
agent that the restart and hook inspection are done so it can verify the loaded components and
immediately resume the first unanswered settings screen. If those hooks are already trusted,
that check is complete; no repeat enable action is needed. A natural-language completion report
is sufficient; a literal `continue` is not required after restart. A second restart is needed
only if the host requests it or verification identifies a reload problem.

For GUI use, open an interactive CLI session in the target repository and complete `/hooks`
inspection and trust there. Then exit the CLI, fully quit and restart the GUI, and
start a new GUI chat in that repository. `/hooks` is not a Codex GUI command. Give only the instructions for the user's
actual host, following the [shared host checkpoint](../../INSTALL_WITH_AGENT.md#4-hook-trust-and-host-restart).
Loading the plugin does not itself establish feature enablement; verify hook trust, repository
flags and the risk receipt. The CLI smoke coverage and its limits are described above.

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

Detection follows executable command positions, including quoted or absolute Git names,
wrappers, nested shells, substitutions, aliases and literal command variables. Quoted examples,
comments and data heredocs are not commits. Visible `commit-tree`, merging `merge`/`pull`,
`cherry-pick`, `revert` and `am` operations that can create commits are refused because the
staged-index review cannot represent them. An inactive target earlier in a compound command
does not hide a later protected target.

This is bounded shell inspection, not a general execution sandbox. Script files, sourced code,
commands assembled from runtime data, `rebase`, and other plumbing may be outside detection.
Inline Python, Node, Perl and Ruby scripts are refused when they mention both a Git commit
producer and a process-spawning primitive; mentioning a commit in a report alone is allowed.
A script that spawns an unrelated process and also mentions a commit can still be refused.
The Codex parser is packaged independently of the Claude runtime; updating one does not update
the other.

The commit parser uses the first nonempty verdict line. An `APPROVED` response may explain
that an invalid input was "rejected" without turning that explanation into a rejection verdict.
Contradictory verdict markers, malformed suggestions sections, and admissions that staged
input could not be inspected still block the commit.

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

For generated data, an optional repository-root `reviewerAgent-ignore.md` supplies the same
skim guidance as the Claude Code plugin. Combine explanatory Markdown with fenced patterns:

````markdown
# Generated records
Check schema and intent; record correctness is checked upstream.

```reviewer-ignore
data/**/*.jsonl
outputs/*.csv
```
````

Patterns support `*`, whole-segment `**`, `?`, character classes such as `[a-z]`, a leading
`/` to anchor at the repository root, and a trailing `/` for a subtree. Unanchored patterns
can match at any depth. Blank lines and `#` comments inside the fences are ignored; multiple
fences form a union. Negation, backslash escapes, embedded `**`, and negated character classes
are rejected. Malformed patterns or invalid UTF-8 block the commit before the reviewer runs.

The effective file comes from the index, never an unstaged or untracked working copy. If the
file is staged, the previous HEAD version supplies this audit's rules and prose; the staged
candidate is still validated. New or changed rules apply only after that commit lands. First
introduction supplies no exclusions, deletion keeps the old rules for that commit, and the
root ignore file itself is always fully reviewed even under a `**` pattern.

Every audit, including a resumed review, receives the current effective guidance. Matched
paths are skimmed for intent, schema, naming and location; the reviewer can fetch suspicious
content with `git diff --cached -- <path>`. Matching retry deltas are omitted before fetching
or inlining their bytes. Paths remain in the retry inventory and the full staged fingerprint;
even an entirely matched staged set still requires approval and is committed in full.
Completed audits and operational outcomes after ignore parsing include `ignore_matched_files`
and `ignore_matched_lines` in the MCP result and retained history. Lines count additions plus
deletions, binary files contribute zero lines, and absent rules produce zero counts. Statistics
use separate deletion/addition entries for renames. Malformed-rule failures record zero counts.
The latest-verdict file retains only the
reviewer's verdict so statistics do not become retry findings.

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

Before an update, the agent must show the existing effective settings and their sources and ask
the user whether to keep or change them, following
[the update settings checkpoint](../../AGENT_UPDATE.md#confirm-existing-settings-with-the-user--both-hosts).
Verify those confirmed settings again after installation and restart.

**Update** an existing installation by pulling the clean checkout and running the local
installer. Keep the release's committed version and `+codex.<timestamp>` metadata. The installer
refreshes the canonical cache even at the same label, preserving displaced live directories
for running sessions. Maintainers refresh the cachebuster when preparing a release; users
do not need to change it during an update.

```bash
git pull --ff-only
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

Use a permanent Python 3.9+ interpreter for the installer. If a temporary test venv is active,
invoke the permanent interpreter by absolute path rather than relying on its `python3` command.
For a dirty checkout, preserve the edits and verify a separate pinned checkout or permanent export.
Do not pull an export or change a frozen candidate's version/cachebuster. If using a different
source directory, follow [the source-link migration below](#changing-the-existing-source-directory).

Before restarting, compare the installed package files with the verified source, check the saved
`python-path`, and compare existing feature settings, receipts and unrelated configuration with
the pre-update baseline. Keep the old source and live cache directories. A fresh process launched
through the installed `.mcp.json` must initialize with the expected version and expose exactly
`review_commit`, `stop_hook`, and `interrupt_stop`; do not invoke the lifecycle tools manually.

Afterwards, follow the [host-specific trust and restart checkpoint](../../INSTALL_WITH_AGENT.md#4-hook-trust-and-host-restart).
For terminal use, restart Codex in the target repository, inspect `/hooks`, and trust only entries
requiring approval. GUI users complete that CLI check, then fully quit and restart the GUI in the
target repository. Already-trusted entries need no repeated action. `/hooks` is not a GUI command. CLI users instead start a fresh interactive CLI session.
Then complete the [post-install verification](#post-install-verification); a successful install
does not prove that an existing task has loaded the new server or hook definitions.

**Disable without uninstalling**, per repository:

```text
$reviewer-agent-codex:reviewer-agent off
$reviewer-agent-codex:stop-continue off
```

For the complete agent procedure, follow
[Codex uninstall and reinstall](../../AGENT_UNINSTALL.md#codex-uninstall-and-reinstall), including
backup, removal checks, a fresh-host checkpoint and the handoff to the initial-install guide.
Run each documented step directly; do not combine uninstall and reinstall in a temporary wrapper.

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

### Changing the existing source directory

An update from a new checkout or permanent export changes the package's absolute path.
The installer refuses when `~/plugins/reviewer-agent-codex` points elsewhere; this is protection
against replacing an unrelated source, not a reason to delete the old checkout or uninstall.

With no review or commit active, record the old target with
`readlink "$HOME/plugins/reviewer-agent-codex"` and confirm that it is this plugin's existing
source. Back up the relevant configuration and feature settings as described in
[the update guide](../../AGENT_UPDATE.md#the-codex-native-host). Keep the old source intact.
Replace the three absolute paths below with a permanent Python interpreter, the verified new
package directory, and the confirmed old package directory. This procedure replaces only the
expected symlink, saves its previous target, and runs the documented installer:

```bash
/absolute/path/to/python3 - /absolute/new/plugins/reviewer-agent-codex /absolute/old/plugins/reviewer-agent-codex <<'PY'
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

source, previous = (Path(arg).expanduser().resolve() for arg in sys.argv[1:])
link = Path.home() / "plugins" / "reviewer-agent-codex"
if not link.is_symlink() or link.resolve() != previous:
    raise SystemExit("Unexpected source path; inspect it instead of replacing it")
manifest = json.loads((source / ".codex-plugin" / "plugin.json").read_text())
if manifest.get("name") != "reviewer-agent-codex":
    raise SystemExit("The new directory is not the reviewer-agent-codex package")
backup = link.with_name(link.name + ".previous-source-" + uuid.uuid4().hex + ".txt")
backup.write_text(str(previous) + "\n")
temporary = link.with_name("." + link.name + ".update-" + uuid.uuid4().hex)
temporary.symlink_to(source, target_is_directory=True)
os.replace(temporary, link)
try:
    subprocess.run([sys.executable, str(source / "scripts" / "install_local.py")], check=True)
except (OSError, subprocess.CalledProcessError):
    temporary.symlink_to(previous, target_is_directory=True)
    os.replace(temporary, link)
    raise
print("Previous source recorded at", backup)
PY
```

If installation fails, the snippet restores the source link. The installer may already have
changed cache/configuration, so inspect its output and the saved baseline; restoring a link is
not a complete installation rollback. After success, perform the byte/state comparisons and
hook-trust/restart checks above. Retain both source directories while a host may still use them.

## Enable in a repository

Commit review and Stop supervision can each be turned on or off independently. Enable either
one, both, or neither; changing one switch does not change the other. Shared model settings and
one repository risk receipt do not require both features to be enabled.

Trusting hooks (including “trust all hooks”) permits execution; it does not accept the disclaimer
or enable either feature. The installing agent must explain this distinction, report the target's
feature/receipt state, and give the user the exact enablement commands below. When the user opts
in without a current receipt, show the short risk notice, disclaimer link and applicable command
from the [shared checkpoint](../../INSTALL_WITH_AGENT.md#5-human-only-checkpoint-and-final-report),
then wait for the user to type it at the prompt. A conversational “yes” is only a preference,
not acceptance. Displaying the command does not invoke it. Only the user can submit
the risk-accepting invocation as their own standalone message in the target repository's host
session. Verify the resulting target, receipt and enabled state; never create the receipt or run
the accepting command on the user's behalf.

Write `reviewerAgent.md` at the repository root, starting from `reviewerAgent.md.template`. Then
invoke the bundled skills:

```text
$reviewer-agent-codex:reviewer-agent on
```

Without a current receipt, this invocation refuses to enable. Read [`DISCLAIMER.md`](DISCLAIMER.md), then invoke:

```text
$reviewer-agent-codex:reviewer-agent on acceptRisk
```

This enables commit review only. If Stop supervision was also selected, enable it separately
with `$reviewer-agent-codex:stop-continue on`. One current receipt covers both features for this
repository. For Stop supervision only, begin with `$reviewer-agent-codex:stop-continue on` instead
of enabling commit review; without a current receipt, its response requires
`$reviewer-agent-codex:stop-continue on acceptRisk`. A changed disclaimer invalidates the old
receipt and requires a new explicit acceptance.

After an exact control command, check the repository named in its response, then type `continue`
as a separate message to resume an agent-assisted setup. The hook ends the control turn without
invoking the agent; do not append `continue` to the control command itself.

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

All five JSON controls and their environment equivalents are listed here. These settings apply
across repositories; feature flags and retained history remain repository-scoped. Missing,
unreadable or malformed JSON is read as empty configuration. Unknown keys are ignored.

| JSON key | Default | Environment override | Values and command behavior |
|---|---|---|---|
| `model` | `gpt-5.6-luna` | `REVIEWER_CODEX_MODEL` | One model token; environment, then reviewer-model command slot, then JSON, then default. |
| `reasoningEffort` | `xhigh` | `REVIEWER_CODEX_EFFORT` | Levels listed above; the same precedence as model. Applies to commit and stop reviews. |
| `commitReviewTimeoutSeconds` | `3600` | `REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS` | Integer 60–7200 seconds. Invalid candidates are skipped. Set via JSON/environment, not the reviewer-model command. |
| `reviewerSessionAuditLimit` | `3` | `REVIEWER_CODEX_SESSION_AUDIT_LIMIT` | Integer 0–1000; 0 disables rotation. Environment, then reviewer-session command slot, then JSON, then default. |
| `reviewHistoryKeep` | `100` | `REVIEWER_CODEX_REVIEW_HISTORY_KEEP` | Retained entries per project, minimum 1. An invalid environment value uses the built-in default; it does not fall through to JSON. |

`clear` for model, effort and session limit removes that command's saved override; environment
and JSON values may still apply. A valid environment override always wins, including over
command changes. Model/effort and rotation-limit resolvers skip invalid candidates.

Additional environment-only controls:

| Variable | Default | Purpose |
|---|---|---|
| `REVIEWER_CODEX_STATE_DIR` | `~/.codex/reviewer-agent-codex` | Configuration and state directory. |
| `REVIEWER_CODEX_BIN` | `codex` on `PATH`, then the local Codex path | Reviewer executable override. |
| `REVIEWER_LIVENESS_TTL_SECONDS` | `86400` | Liveness-marker expiry in seconds, minimum 60. |
| `REVIEWER_STOP_MAX_NUDGES` | `10` | Continuation budget per operator request, minimum 1. |

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

After two consecutive blocking reviews without new operator input or observed tool/subagent
activity, the next Stop receives a fresh completion reassessment. It replaces that Stop's normal
resumed review, so there is still one reviewer call per Stop. Further unchanged continuations
also receive fresh assessments until stopping is allowed, activity resets the chain, or the
existing nudge cap is reached. The default cap remains ten.

The fresh reviewer receives the current operator context, plan, final response, and background
markers, plus an instruction to reassess the requested scope independently. It has no previous
supervisor conversation: earlier directives cannot become operator authorization. It can approve
completed work or a genuine operator-only dependency; lack of tool use, repeated questions, and
claims of completion do not automatically permit a stop. Authorized unfinished work still gets a
continuation. This check uses the configured reviewer model and effort, read-only access, and the
normal stop-review timeout. The plugin never saves or resumes its disposable thread ID, and the
check does not replace the persistent stop or commit-reviewer threads.

The chain is stored per project and developer session, using operator/work generations captured
by lifecycle hooks rather than rollout parsing. Changing only a host turn ID does not reset it.
An allowed or cancelled stop ends the chain, and session cleanup removes it. Fresh results pass
through the same cancellation, fail-open failure handling, new-input/work validation, and exact
background-marker reconciliation as ordinary stop reviews.

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

The native Stop hook starts its completion review immediately. The blocking reading window
has been removed: Codex does not provide a documented signal confirming that the answer has
reached the screen, so waiting inside the hook could add a silent minute without giving the
operator time to read or reply. Removing the wait does not establish that intermittent missing
or disappearing text in the desktop app is resolved.

Legacy `stopContinueDelaySecs` configuration and `REVIEWER_STOPCONTINUE_DELAY_SECS` values
are ignored. Old `$reviewer-agent-codex:stop-continue delay` invocations (including set, status
and clear) and `python3 scripts/toggle.py stop delay` report that the feature was removed and
leave settings unchanged. The Stop hook's 360-second timeout covers the 300-second review
budget and overhead. After upgrading, trust the modified Stop hook through interactive CLI
`/hooks` and restart the host.

**Currently, in Codex, you must press Escape to interrupt a running completion review.**
Typing or submitting a reply does not interrupt the check. Codex may queue
the reply until the check finishes, as observed during desktop acceptance.

The plugin receives new input through the
[documented host hooks](https://learn.chatgpt.com/docs/hooks), but it cannot act on a reply before
Codex delivers it. Use Escape rather than submitting a reply when you want to cancel the check.

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
active, stopContinue distinguishes temporary background waiting from task completion. If a
monitor, scheduled wake-up, or host completion notification is already arranged and no other
authorized work can proceed now (or the operator explicitly requested the pause), it permits
the turn to end with `STOP_WAIT`. A future wake-up test is verified on the resumed turn. Ordinary
unified-exec sessions do not by themselves establish an automatic wake-up. If no resumption
mechanism is arranged or actionable foreground work remains, the supervisor directs that work.
Before reviewing a shell marker,
the Stop hook waits 250 milliseconds and rechecks once; only a concurrently delivered
`PostToolUse` clear changes that immediate outcome.

Codex exposes no lifecycle API for enumerating a unified-exec session whose `session_id` was lost,
and either a shell completion event or a `SubagentStop` event can be missed. Sandboxed hooks cannot
safely use the host process table or collaboration registry to prove whether such a marker is still
live. A stop with shell and/or subagent markers therefore routes the exact `(kind, id)` set directly
through the same minimal, cancellable completion supervisor. For a completion ruling (`STOP_OK`), the supervisor may approve shell markers only when
the proposed final states that the named work was inspected or polled and no process or
unified-exec session remains; for subagent markers it must state that the collaboration registry
was reconciled and no live subagent remains. Otherwise it directs the developer agent to perform
the missing polling or reconciliation after the blocking call returns. `STOP_WAIT` preserves
the existing shell and subagent markers for later lifecycle events and reviews; it is neither
a declaration of completion nor a failure. Completion approval or fail-open clears
only the exact adjudicated `(kind, id)` set. Cleanup is best-effort so a state I/O error cannot
defeat StopContinue's fail-open contract. New work that appears during the ruling prevents the stop.

A yielded unified-exec response containing `session_id` is still active. Code-mode orchestration
must poll that exact session with `write_stdin` to observe completion, unless it is temporarily
yielding to an arranged resumption mechanism; waiting only on the
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
when no working Python 3.9+ can be found, or the MCP server fails its startup check.
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
