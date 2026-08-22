# Install reviewer-agent with an agent


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

This is the single handoff file for agent-assisted installation of either the Claude Code plugin
or the Codex plugin. It minimizes manual setup without pretending that an agent can authenticate
accounts, approve its own elevated writes, restart its host, trust lifecycle hooks, or accept the
software's risks on a human's behalf.

## What the human must do

Before starting:

1. Install and authenticate the developer host to be protected: Claude Code or Codex.
2. Confirm that the machine's Git client can read `rladr-org/reviewer-agent`; authentication is
   needed only when installing from a private fork or mirror.
3. For Claude Code only, install and authenticate at least one desired reviewer backend: `codex`,
   `pi`, or the `claude` CLI you are already running. They are alternatives; a Pi-only setup does
   not need Codex, and the `claude` backend needs no additional install. Its usage, quota, billing
   and account consequences are controlled by Anthropic and the user's plan.
4. Start the developer host in the repository you want to protect.

Then give the agent this prompt:

```text
Clone https://github.com/rladr-org/reviewer-agent.git into a new user-owned directory outside this target repository if needed, then read `INSTALL_WITH_AGENT.md` completely and follow it to install reviewer-agent for this repository. Use the current developer host. Ask me for any required backend or feature choice, but otherwise perform and verify every step you are authorized to do.
```

If a checkout already exists, replace the clone clause with its absolute path. During installation,
the human must approve writes outside the repository when the host requests approval. At the final
human-only checkpoint, restart the developer host. For Codex, also run `/hooks` and trust the
`reviewer-agent-codex` hooks. Those actions cannot safely be delegated.

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

If the installer repository is not present, clone it into a new, user-owned path outside
`<TARGET_REPO>` after obtaining any required network approval. Refuse to overwrite or reuse an
unrelated non-empty path. If it is already present, preserve local changes; update it with
`git pull --ff-only` only when the checkout is clean and the operator requested the current release.

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

Run the applicable package tests before installing. Do not continue when a required check fails.

### 4A. Claude Code installation

Follow `AGENT_INSTALL.md`, including legacy-hook detection, backend selection, Pi extension setup
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
`acceptRisk` command from a new, trusted Codex thread. Do not substitute `codex plugin add`: the
local installer also configures the blocking reviewer MCP namespace as a direct-only tool.

### 5. Human-only checkpoint and final report

After all agent-executable checks pass, tell the human exactly what remains:

- Claude Code: restart Claude Code in `<TARGET_REPO>` so the installed hooks are loaded.
- Codex: start a new Codex thread in `<TARGET_REPO>`, run `/hooks`, inspect the
  `reviewer-agent-codex` entries, and trust them.

Then tell the human to invoke plain `on` first. It must refuse without enabling and display the
exact accepting form. After reading `DISCLAIMER.md`, the human may type that exact form:

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

Report the host, selected backend where applicable, requested features, canonical target root,
installed version, test results, every external path changed, and any warning. State that the first
real commit after operator acceptance is the end-to-end gate verification. Say plainly: enablement
and live gate verification are pending until then. Do not claim hooks ran in the old thread: both
hosts load newly installed plugin components at a new-session boundary.
