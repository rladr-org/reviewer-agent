# Install reviewer-agent with an agent


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

This is the single handoff file for agent-assisted installation of either the Claude Code plugin
or the Codex plugin. It minimizes manual setup without pretending that an agent can authenticate
accounts, approve its own elevated writes, restart its host, trust lifecycle hooks, or accept the
software's risks on a human's behalf.

## What the human must do

Before starting:

1. Install and authenticate the developer host to be protected: Claude Code, or Codex CLI
   (optionally with the Codex GUI). The CLI is required for Codex hook trust.
2. Confirm that the machine's Git client can read the configured source, such as
   `rladr-org/reviewer-agent`. If HTTPS requests credentials or SSH reports an authentication
   failure, verify access and use the operator's authenticated Git transport. This applies to
   the canonical source as well as forks and mirrors; a clone failure alone does not establish
   repository visibility. Preserve the working transport and SSH configuration for later updates.
3. For Claude Code only, install and authenticate at least one desired reviewer backend: `codex`,
   `pi`, or the `claude` CLI you are already running. They are alternatives; a Pi-only setup does
   not need Codex, and the `claude` backend needs no additional install. Its usage, quota, billing
   and account consequences are controlled by Anthropic and the user's plan.
4. Start the developer host in the repository you want to protect.

For the Codex package, the GUI has been exercised as the developer host. Stop supervision has also
been exercised with Codex CLI 0.154.0 on macOS; this does not establish CLI commit-gate or general
terminal UI coverage. See the package's
[CLI completion checks](plugins/reviewer-agent-codex/README.md#cli-completion-checks).

Then give the agent this prompt:

```text
Clone https://github.com/rladr-org/reviewer-agent.git into a new permanent user-owned directory outside this target repository if needed, then read `INSTALL_WITH_AGENT.md` completely and follow it to install reviewer-agent for this repository. Use the current developer host. Ask me for any required backend or feature choice, but otherwise perform and verify every step you are authorized to do.
```

If a checkout already exists, replace the clone clause with its absolute path. During installation,
the human must approve writes outside the repository when the host requests approval. At the final
human-only checkpoint, restart the developer host. For Codex, hook trust must be completed in the
interactive CLI even when the human uses the GUI: run `codex` in a terminal, enter `/hooks`, and
trust the `reviewer-agent-codex` hooks. Then fully restart the GUI, if that is the surface being
used. Those actions cannot safely be delegated.

## Procedure for the installing agent

Read this entire file before acting. Do not ask the human to run a command that the agent can run,
except that only the human may make the exact `acceptRisk` invocation described below.
Use the host's normal approval mechanism for external writes, and stop on a failed verification
rather than declaring a partial fail-closed gate installed.

### 1. Preserve the two repository identities

Record the canonical Git root of the initial working directory as `<TARGET_REPO>`. That is the
repository to protect. Resolve the checkout containing this file as `<INSTALLER_REPO>`.

Do not treat the installer checkout as the target repository. Changing a shell subprocess's
directory does not change the developer host's project identity. Never enable the gate for
`<INSTALLER_REPO>` unless it is also the operator's explicit target.

If the installer repository is not present, clone it into a new, permanent user-owned path outside
`<TARGET_REPO>` after obtaining any required network approval. Refuse to overwrite or reuse an
unrelated non-empty path. If it is already present, preserve local changes; update it with
`git pull --ff-only` only when the checkout is clean and the operator requested the current release.

Do not use a session scratch directory or temporary checkout. Claude's local marketplace points
to this checkout, and the Codex installer creates `~/plugins/reviewer-agent-codex` as a source
symlink to its package directory. Moving or deleting the checkout breaks refresh/update and
source fallback even if an existing immutable Codex cache continues working. Keep any required
SSH host alias, key selection, or credential-helper configuration available to later updates;
do not embed credentials in the remote URL or logs.

### 2. Resolve choices once

Use the current developer host unless the prompt explicitly names another one. Installation enables
the commit reviewer. Stop supervision remains a separate choice. Under Claude Code, `codex` and
`pi` and `claude` are alternative backends and the operator must choose one.

If the prompt did not specify the Claude backend or whether to enable stop supervision, detect what
is available. Ask for all missing choices in one message. Do not silently choose a backend, do not
install Codex for a Pi user, and do not enable stop supervision merely because the commit gate was
requested.

### 3. Inspect changes and prerequisites

Before any write outside `<INSTALLER_REPO>` or `<TARGET_REPO>`, summarize the paths that will change
and request the normal host approval. Preserve existing configuration and project briefs. Confirm
the platform, Python version, Git repository, host authentication, and backend authentication using
the applicable guide:

- Claude Code: read `AGENT_INSTALL.md` completely.
- Codex: read `plugins/reviewer-agent-codex/README.md` completely, including its install and enable
  sections.

Use an available Python 3.9+ interpreter; no named conda environment is required. In the commands
below, substitute its executable for `python3`, replace the angle-bracket paths, and choose a
new `<TEST_VENV>` outside the checkout. Development dependencies are used only by tests, not by
the hooks or MCP runtime:

```bash
python3 -m venv <TEST_VENV>
<TEST_VENV>/bin/python -m pip install -r <INSTALLER_REPO>/requirements-dev.txt
```

Run the applicable package tests before installing, with an explicit working directory in the
same shell call:

```bash
# Claude Code package
cd <INSTALLER_REPO> && <TEST_VENV>/bin/python -m pytest -p no:cacheprovider
# Codex package
cd <INSTALLER_REPO>/plugins/reviewer-agent-codex && <TEST_VENV>/bin/python -m pytest -p no:cacheprovider
```

These suites can take several minutes. Use the host's background execution support and retain
the final exit status when its foreground timeout is shorter; do not install until the applicable
suite finishes successfully. A temporary test environment is fine; the plugin checkout must be
permanent. Ensure `python3` is Python 3.9+ on the developer host's launch PATH as well: activating
a test environment in one shell does not configure the hooks of a running GUI or another shell.

### 4A. Claude Code installation

Follow `AGENT_INSTALL.md`, including existing-hook checks, backend selection, Pi extension setup
when selected, and project-specific brief creation. Use the non-interactive host CLI for the plugin
installation:

```bash
claude plugin marketplace add <INSTALLER_REPO>
claude plugin install reviewer-agent@reviewer-agent
claude plugin list
```

Refuse to overwrite `<TARGET_REPO>/reviewerAgent.md`. If it is absent, copy
`<INSTALLER_REPO>/reviewerAgent.md.template` there and edit it to describe the target repository's
real fitness criteria, commands, and known defect classes. Configure the selected backend exactly
as `AGENT_INSTALL.md` specifies.

Verify that the selected backend reads back correctly. Do not run either toggle script: the
operator must read `DISCLAIMER.md` and personally invoke the exact `acceptRisk` command after the
host restart. Do not make a demo commit in the target repository unless the operator explicitly
asks.

### 4B. Codex installation

From `<INSTALLER_REPO>`, use the checkout installer rather than a bare marketplace command:

```bash
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

Refuse to overwrite `<TARGET_REPO>/reviewerAgent.md`. If it is absent, copy
`<INSTALLER_REPO>/plugins/reviewer-agent-codex/reviewerAgent.md.template` there and edit it for the
target repository. Do not run the source checkout's toggle CLI: it cannot record operator risk
acceptance. The operator must read the plugin's `DISCLAIMER.md` and personally invoke the exact
`acceptRisk` command from a fresh Codex GUI chat or interactive CLI session after completing hook
trust in the CLI, if no current acceptance receipt exists. Do not substitute `codex plugin add`:
the local installer also configures the blocking reviewer MCP
namespace as a direct-only tool.

Verify the copied Codex brief contains actual project guidance and no remaining
`Project-specific guidance (replace this paragraph)` placeholder. For existing briefs copied
from an older template, remove the introductory copy/enable instructions while preserving
the project's rules. Do not overwrite a customized brief; explain any required edits.

For an upgrade, rerun this same installer and preserve the target repository's feature settings,
risk-acceptance receipt, and project brief. Completion supervision runs only through Codex's
automatic `Stop` hook. `Interrupt` cancels active reviews and does not start a review. Neither
`stop_hook` nor `interrupt_stop` should be called manually.

### 5. Human-only checkpoint and final report

After all agent-executable checks pass, tell the human exactly what remains:

- Claude Code: restart Claude Code in `<TARGET_REPO>` so the installed hooks are loaded.
- Codex: run `codex` in a terminal to open an interactive CLI session, then run `/hooks`, inspect
  the `reviewer-agent-codex` entries, and trust any entries requiring approval. `/hooks` is not a
  Codex GUI command. If the operator uses the GUI, exit the CLI, fully quit and restart the GUI,
  then start a new chat in `<TARGET_REPO>`. For CLI use, start a fresh interactive CLI session there.
  Explain that CLI smoke
  coverage is limited to stop supervision and does not cover every terminal UI or the commit gate.

For a first installation without a current repository acceptance receipt, tell the human to
invoke plain `on` first. It must refuse without enabling and display the exact accepting form.
After reading `DISCLAIMER.md`, the human may type that exact form:

```text
/reviewerAgent on
/reviewerAgent on acceptRisk

$reviewer-agent-codex:reviewer-agent on
$reviewer-agent-codex:reviewer-agent on acceptRisk
```

Show only the pair for the installed host. One current repository receipt covers both features, so
the human may then use plain `/stopContinue on` or
`$reviewer-agent-codex:stop-continue on` if stop supervision was selected. If stopContinue is
enabled first, its plain `on` response supplies its own `on acceptRisk` form.
An upgrade with a current receipt does not require accepting again; preserve enabled and disabled
features unless the operator requests a change.

For Codex stop supervision, verify the connected reviewer MCP server and automatic hook mappings
after reload: `Stop` calls `reviewer-agent.stop_hook`, and `Interrupt` calls
`reviewer-agent.interrupt_stop`. The server exposes `review_commit`, `stop_hook`, and
`interrupt_stop`. A host with `mcp_tool` lifecycle-hook support is required.
Missing hooks or a disconnected server can leave
Stop supervision inactive without preventing the turn from ending.

When stop supervision was selected and enabled, follow the package's
[post-install verification](plugins/reviewer-agent-codex/README.md#post-install-verification).
Record review or lifecycle evidence for approval of a completed answer, continuation of unfinished
authorized work, and both interruption cases. Run checks through actual host events, never manual
lifecycle-tool calls. An absent continuation prompt alone does not prove a review ran. Approval
adds no continuation message; a continuation directive can appear in the conversation, and the
desktop app may collapse earlier messages. Installation cannot guarantee hidden feedback.

Report the host, selected backend where applicable, requested features, canonical target root,
installed version, test results, every external path changed, and any warning. State that the first
real commit after operator acceptance is the end-to-end commit-gate verification. Report
enablement, commit-gate verification, and Stop verification separately, marking any unperformed
check as pending. Do not infer that an existing task has reloaded from the installer succeeding;
verify the loaded components after restarting the host, or use a fresh session.
