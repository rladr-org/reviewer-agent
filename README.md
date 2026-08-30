# What is it? 

This package provides two major functions: 
- Reviewer Agent - does automated plan and code reviews, gating each commit until issues that it finds are resolved
- Stop Continue - examines the project plan, task list, and whether there are background tasks running that the agent is waiting for, and decides if the agent is allowed to stop or if not, what it should do next

Works with Claude Code and Codex, and uses Codex, Pi (with any model), or Claude Code itself as the reviewer and stop continue agent (with some limitations listed below).

# Inspiration and Justification

I have been using LLMs to do coding since it was possible to do so, and have tried to use every major model. 
I've also had each major model do plan reviews and code reviews on every other model. I've found that it's very beneficial to let different models review each other's plans because they tend to find serious problems that would have wasted a lot of time. 

## Plan and Code Review gates:

- A model that does not do the coding but only reviews plans or code can find problems that the coding agent has missed
- Different models have different strengths and using more than one model can produce better plans and code with less bugs

So this project sets up a commit gate which does not allow a commit to proceed until a separate agent (that you can choose) 
completes a plan or code review. If the review agent specifies issues to fix, they must be fixed by the coding agent before 
the commit will be allowed to proceed. 

An interesting finding is that a small, inexpensive model like gpt-5.6-luna (which I run on xhigh and fast mode) when used as 
a reviewer agent, finds numerous serious issues, omissions, and errors even when the coding agent is Fable 5, Opus 5, or 
gpt-5.6-sol, all on xhigh mode. *Note that you can use any model as a reviewer!* 

It can often take 3 to 9 commit attempts for Fable to get rid of all the bugs found by gpt-5.6-luna, and if you ask it
to weigh in on whether the required fixes are valid or not, it overwhelmingly agrees that they are real issues. 

## Don't stop - Continue! 

Another issue is that even the best coding agents will work for a while (sometimes a long while) and then stop. 
Sometimes they say "this is a good stopping point" (NO! There is no good stopping point!), sometimes they ask the 
user a question, or for permission to continue, even if permission was already given to complete an entire set of tasks, or the question was already decided upon in an established plan. And sometimes they just... stop.

Various methods have been used to prevent this, including Ralph Loops and Claude Code's /goal, but there are some advantages to having another agent evaluate each stop, consider the project's plans, consider the task list, consider what the user said in the  session, and then decide if a stop is allowed, or if not tell the agent what it is supposed to do next (which may include having the supervisor agent answer a question or give permission on behalf of the user). 

So one separately controllable feature of this package is stop-continue which does exactly that, using the same model 
as reviewer-agent but a separate session. 

# Project state

These are tools I have been using, debugging, and improving for a couple of months, and they generally work well for me.
Many bugs have been fixed, and many edge cases addressed, but undoubtedly more will be found. 
It's easy to install and easy to remove, so give it a spin and please report any issues you run into. 

## A note about suggested workflow (not enforced by this tool)

If you want to gain the most benefit from using an automated reviewer agent, what I've found to work well is
the following process, which would be specified in CLAUDE.md or AGENTS.md so that the coding agent follows it: 

- Create a plan 
- Commit the plan and address any reviewer-found issues
- Create the tests (TDD - Test Driven Development)
- Commit the tests and address any reviewer found issues
- Create the code to pass the tests
- Commit the code and address any reviewer found issues
- Run the code and fix any bugs
- Commit the code 
  
Along with this, I find it useful to let the coding agents have four .MD files:
- PLAN.md - the overall development plan
- HANDOVER.md - very regularly updated file that is kept under 400 lines or so, that only contains status and context sufficient for the agent to continue after compaction or if re-started after /clear
- CODING_LOG.md - lets the agent keep track of coding decisions (if this is not included the agents tend to clutter up HANDOVER.md with this)
- FINDINGS.md - results of tests or other information the agent wants to keep track of that should not clutter up HANDOVER.md

## Before you install: what a reviewer is allowed to do

> **USE AT YOUR OWN RISK — NO WARRANTY OF ANY KIND.**
>
> This software runs an autonomous AI agent inside your development environment. It reads your
> source code, sends it to a third-party AI provider (of your choosing), and **on the Codex backend it executes
> commands in your repository**. It can consume paid quota. It may approve defective or insecure
> code, and it can miss vulnerabilities entirely. Its output may be treated as an instruction or
> permission by another AI agent and may cause destructive, costly, irreversible or externally
> visible actions without another human confirmation.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
> BUT NOT LIMITED TO THE IMPLIED WARRANTIES OF **MERCHANTABILITY**, **FITNESS FOR A PARTICULAR
> PURPOSE**, AND **NON-INFRINGEMENT**. TO THE MAXIMUM EXTENT PERMITTED BY LAW THE AUTHOR AND
> CONTRIBUTORS ACCEPT **NO LIABILITY** FOR ANY DAMAGE ARISING FROM ITS USE — INCLUDING LOST,
> DELETED, OVERWRITTEN OR CORRUPTED CODE AND DATA; DEFECTS OR SECURITY VULNERABILITIES IT
> INTRODUCED, APPROVED OR FAILED TO DETECT; ANYTHING EXECUTED BY OR AT ITS DIRECTION; ANY ACTION
> TAKEN IN RELIANCE ON ITS VERDICTS, RECOMMENDATIONS, ANSWERS, PERMISSIONS OR DIRECTIONS; DISCLOSURE
> OF YOUR CODE TO ANY PROVIDER; OR ANY EFFECT ON YOUR PROVIDER ACCOUNT, SUBSCRIPTION OR CHARGES.
>
> **YOU ARE SOLELY RESPONSIBLE FOR EVERYTHING THIS SOFTWARE AND THE AI AGENTS IT INVOKES DO IN
> YOUR ENVIRONMENT, AND FOR ALL CODE COMMITTED WITH ITS INVOLVEMENT.** Keep independent backups.
> Do not point it at repositories you do not trust. Do not rely on it where failure would cause
> harm.
>
> Full terms: **[`DISCLAIMER.md`](DISCLAIMER.md)** and [`LICENSE`](LICENSE) (MPL-2.0, §6 and §7).
> Every document in this repository, including [`SECURITY.md`](SECURITY.md) and
> [`PRIVACY.md`](PRIVACY.md), is informational only and must not be relied upon.

**The intended execution boundary** In the current implementation, a Codex commit
reviewer is configured to run commands in a `workspace-write` sandbox with approvals disabled and
`.git` read-only. The Pi and Claude reviewer configurations are intended to provide repository-read
tools without a shell or edit tool. These are implementation descriptions, not security guarantees;
verify the source and the behavior of the installed host versions before relying on them. Read
[`SECURITY.md`](SECURITY.md) and [`PRIVACY.md`](PRIVACY.md) before enabling the software.

### What the reviewer actually does

When the agent runs `git commit`, the commit is held and a **separate agent** is handed the staged
diff. What that agent can then do depends on which backend you chose:

**Codex** — an agentic review. It reads the staged patch, and then explores: opening the files
the diff touches and the code they depend on, reading tests, and, during a commit audit,
**running commands in the repository** — typically the repository's test suite, because the most
useful way to judge a change is to see whether it still passes. The current invocation requests a
`workspace-write` sandbox with the working tree and a temporary directory writable and `.git`
read-only; that configuration is intended to prevent the reviewer from creating the commit. It returns
one of two answers: `APPROVED` (optionally with non-blocking `SUGGESTIONS:`), or `REJECTED:`
followed by a numbered list of required fixes with `file:line` evidence. The developer host
performs the commit only after an approval response. Briefing, question answering and stop
supervision request read-only operation.

**Pi** — intended as a static review. The current invocation omits shell, write, and edit tools;
it supplies the staged patch through a constrained Git wrapper and allows repository reads for
context. This configuration does not provide a test-running path and does not use Codex as a
fallback.

**Claude** — a static review by a second Claude Code process, for people who would rather not
install a second vendor's CLI. It is **opt-in and never the default**. The child authenticates
through the installed `claude` CLI, so usage, quota, billing and account consequences are governed
by the provider and the user's plan. The current child-process configuration requests `Read`,
`Grep` and `Glob`, denies other tools by name, and uses `--safe-mode`; treat those as configuration
details to verify, not as a guarantee that a provider or later CLI version will behave identically.

Either way the reviewer is a **different session from the developer agent**. One reviewer
conversation is kept per project, developer session and role, briefed with that project's
`reviewerAgent.md` when it is created. It persists through rejected commit attempts, so when the
developer agent fixes its findings and tries again, it remembers what it asked for. By default it
rotates after three completed non-retry audits; every attempt after a rejection is a retry until approval and
does not advance that counter. `/reviewer-agent:reviewerSession rotate` retires it immediately.

**Status: public beta. Nothing here is supported, and nothing is guaranteed to work.** There is
no supported configuration, no supported platform, and no supported host version — "supported"
would imply an undertaking, and there is none.

What can be said is how much use each combination has actually had, which is a fact about the
past rather than a promise about your machine:

| Developer host | Reviewer backend | What was actually done |
|---|---|---|
| Claude Code | `codex` | used daily in a private installation, macOS Tahoe |
| Codex | Codex child session | used daily in a private installation, macOS Tahoe |
| Claude Code | `pi` | automated release matrix on macOS; no sustained use |
| Claude Code | `claude` | automated release matrix on macOS; no sustained use |

The `pi` and `claude` backends pass the same automated live matrix as the other two — commit
audits, rejections and approvals, stop supervision, cancellation, process cleanup — but a matrix
run is not the same as living with something. Nothing selects them for you: `codex` is the
fallback when nothing is configured.

Nothing has been installed or used on any platform other than macOS Tahoe. Linux is exercised by
CI unit tests only (Ubuntu, Python 3.9/3.12/3.14), with no installation behind it; WSL2 has had
neither; other macOS versions have not been tried. **Even the top two rows are not guaranteed to
work** — they worked on my machine but may not work on yours.

**No maintenance or response is promised.** This is a personal project published as-is. Nothing
here undertakes that it is maintained or monitored, that an issue, pull request or security report
will be read, answered or acted upon, that any defect or vulnerability will ever be fixed, or that
any further release will appear. Reports are welcome and may be ignored; the project may be
archived at any time without notice. See [`DISCLAIMER.md`](DISCLAIMER.md).

Reports are still worth filing — see [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`CHANGELOG.md`](CHANGELOG.md).

### installation 
Automated agent-assisted installation is [available](https://github.com/rladr-org/reviewer-agent/tree/main#agent-assisted-installation)

# reviewer-agent

Reference documentation for both packages: what each host supports, how to install and remove
it, and how the reviewer behaves.

The two host packages share review standards, retry context and project briefs, but use
different hooks and runtime interfaces, and are installed independently.

## Host and backend support

| Developer host | Capability | Reviewer backend | Status |
|---|---|---|---|
| Claude Code | staged commit audit | selected `codex`, `pi` or `claude` backend | implemented |
| Claude Code | timed-out `AskUserQuestion` answer | selected `codex`, `pi` or `claude` backend | implemented |
| Claude Code | `stopContinue` supervision | selected `codex`, `pi` or `claude` backend | implemented |
| Codex | staged commit audit | Codex child session | implemented |
| Codex | stop supervision | separate Codex child session | implemented |
| Codex | `AskUserQuestion` | — | not implemented by the Codex host |

**"Implemented" means the code exists and the tests pass. It is not a claim of support and not a
promise that it works for you.** Every row above is covered by the unit suites. The automated live
matrix, which drives the real reviewer CLIs, additionally covers commit audits and stop
supervision on every backend — the timed-out `AskUserQuestion` path is not in it, because the
timeout is produced by the host rather than by anything this harness can trigger, so that path has
unit tests and hand checks only.

Selecting `pi` in Claude Code routes commit audits, timed-out `AskUserQuestion` answers, and `stopContinue` through Pi.
Selecting `pi` does not require or launch Codex. The same is true of `claude`.

The three backends differ in what a review is *allowed to do*, and that is the difference to
understand before choosing one:

| Reviewer backend | Execution boundary |
|---|---|
| `codex` | The current commit-audit invocation requests `workspace-write` with `approval_policy="never"` and read-only Git metadata. Briefing, question answering, and stop supervision request read-only operation. |
| `pi` | The current `-t` allowlist names read/search tools plus `git_read` and omits shell, write, and edit tools. The intended result is a static audit of the staged patch. |
| `claude` | The current child invocation requests `--safe-mode`, `--permission-mode dontAsk`, and an allowlist of `Read`, `Grep`, and `Glob`, with common execution and mutation tools denied by name. The staged patch is supplied in the prompt. |

The current `pi` and `claude` configurations are intended not to grant command execution and do not
configure a hidden Codex fallback. A missing Pi extension is handled as a review failure.
[`SECURITY.md`](SECURITY.md) states what bounds each path -- timeout, cancellation,
process-tree cleanup, sandboxing and permissions.

The Codex package currently supports no `pi` backend. The Codex package does not implement `AskUserQuestion`.
Its commit reviewer and stop supervisor both use isolated Codex child sessions.

The packages are installed independently:

- The repository root is the Claude Code plugin.
- [`plugins/reviewer-agent-codex`](plugins/reviewer-agent-codex) is the Codex plugin.

## Capabilities and failure behavior

The Claude Code plugin provides three per-repository capabilities:

1. **Commit gate.** A `git commit` is intercepted and the staged set is audited first. Approval
   permits the command; rejection or an operational review failure blocks it. The gate is
   fail-closed.
2. **Timed-out question answering.** Claude Code displays `AskUserQuestion` normally. If the
   operator answers, the reviewer is not involved. If the native away-from-keyboard timeout
   expires with no answer, the selected reviewer answers and the developer agent continues. This
   path is fail-open if the reviewer fails.
3. **`stopContinue` supervision.** When enabled separately, the selected reviewer checks each
   proposed stop. It returns `STOP_OK` when the task is complete or gives a continuation directive
   when work remains. This path is fail-open and has a per-turn nudge cap.

The Codex package provides the commit gate and stop supervisor. Its blocking MCP calls suspend the
developer agent until the independent reviewer returns or the call is cancelled.

## Agent-assisted installation

The shortest installation route delegates every executable step to the current coding agent. The
human only supplies credentials and choices, approves external writes, and performs the final
restart and hook-trust actions that an agent cannot perform for them.

1. Install and authenticate Claude Code or Codex. For Claude Code, also install and authenticate
   the chosen reviewer backend (`codex` or `pi`); the `claude` backend needs nothing beyond the
   authenticated Claude Code you are already running.
2. Confirm that Git can read the reviewer-agent repository; authentication is needed only when
   installing from a private fork or mirror.
3. Start the developer host in the repository you want to protect.
4. Paste this prompt:

   ```text
   Clone https://github.com/rladr-org/reviewer-agent.git into a new user-owned directory outside this target repository if needed, then read `INSTALL_WITH_AGENT.md` completely and follow it to install reviewer-agent for this repository. Use the current developer host. Ask me for any required backend or feature choice, but otherwise perform and verify every step you are authorized to do.
   ```

5. Approve the requested writes outside the repository. When the agent finishes, follow its one
   restart instruction; for Codex, also run `/hooks` and trust the displayed reviewer hooks.

The installing agent's complete procedure is [`INSTALL_WITH_AGENT.md`](INSTALL_WITH_AGENT.md).
It covers both host packages, prevents the installer checkout from being mistaken for the target,
and leaves only the unavoidable human-only checkpoint.

## Claude Code installation

Prerequisites:

- macOS (where it has actually been run), or native Linux/WSL2 (never run there), with a
  Claude Code that has plugin support;
- `python3` 3.9 or newer on `PATH`; the runtime is standard-library-only;
- one authenticated reviewer backend: `codex`, `pi`, or the `claude` CLI you already have.

Install from the marketplace:

```text
/plugin marketplace add rladr-org/reviewer-agent
/plugin install reviewer-agent@reviewer-agent
```

Restart Claude Code after installation. Claude Code snapshots hooks when a session starts; an
existing session may otherwise keep the previous hook set.

### Upgrading from the standalone Claude Code install

Do not keep legacy standalone hooks and plugin hooks enabled together. Claude Code runs additive
hook registrations, so both copies will handle every matching event.
In particular, older standalone `SessionStart` hooks always launch Codex and therefore defeat a Pi-only configuration.

Before enabling the plugin, inspect `~/.claude/settings.json` for commands pointing to
`~/.claude/reviewerAgent/` or `~/.claude/hooks/bash_gate.py`:

```bash
rg 'reviewerAgent|bash_gate\.py' ~/.claude/settings.json
```

Remove the standalone reviewer entries before restarting Claude Code. Inspect `bash_gate.py`
before replacing it: older installations may chain a separate preflight hook through that wrapper,
which should be preserved as its own hook if it is still wanted. Keep a settings-file backup and
verify `/hooks` after the restart.

In each repository to protect:

1. Copy `reviewerAgent.md.template` to `reviewerAgent.md` at the repository root and edit it for
   that project. `/reviewer-agent:reviewerAgent on` refuses to enable a repository without this
   brief.
2. Select and verify the backend as described in [Claude Code backends](#claude-code-backends).
3. Enable the commit gate and timed-out question answering:

   ```text
   /reviewer-agent:reviewerAgent on
   ```

   The first invocation does not enable anything. Read [`DISCLAIMER.md`](DISCLAIMER.md), then
   accept its risks explicitly and enable:

   ```text
   /reviewer-agent:reviewerAgent on acceptRisk
   ```

4. Optionally enable stop supervision:

   ```text
   /reviewer-agent:stopContinue on
   ```

   The acceptance receipt is shared by both features in this repository. If stopContinue is the
   first one enabled, its plain `on` invocation gives the corresponding
   `/reviewer-agent:stopContinue on acceptRisk` instruction.

The flags are stored by canonical repository root, apply to linked worktrees, and persist across
Claude Code restarts. `/reviewer-agent:reviewerAgent off` and `/reviewer-agent:stopContinue off`
disable them independently.

The successful reviewerAgent enabling invocation also adds `askUserQuestionTimeout: 60s` to the
user’s `~/.claude/settings.json` when the setting is absent. It never replaces an existing value.
The setting takes effect in a new Claude Code session.

For agent-assisted installation, start with [`INSTALL_WITH_AGENT.md`](INSTALL_WITH_AGENT.md). The
Claude-specific unattended procedure it invokes is [`AGENT_INSTALL.md`](AGENT_INSTALL.md).

## Codex installation

The Codex package has a separate operating guide at
[`plugins/reviewer-agent-codex/README.md`](plugins/reviewer-agent-codex/README.md). Its installer
registers both the plugin and its blocking MCP server:

```bash
git clone https://github.com/rladr-org/reviewer-agent.git
cd reviewer-agent
python3 plugins/reviewer-agent-codex/scripts/install_local.py
```

When installing from a private fork or mirror, grant the installer’s user access before cloning.
After installation:

1. Start a new Codex thread.
2. Run `/hooks`, inspect the `reviewer-agent-codex` hooks, and trust them.
3. From the repository to protect, enable the desired features:

   ```text
   $reviewer-agent-codex:reviewer-agent on
   ```

   The first invocation refuses to enable anything. Read the plugin's `DISCLAIMER.md`, then use:

   ```text
   $reviewer-agent-codex:reviewer-agent on acceptRisk
   $reviewer-agent-codex:stop-continue on
   ```

   One current receipt covers both features in that repository. If stopContinue is enabled first,
   its plain `on` invocation instead requires
   `$reviewer-agent-codex:stop-continue on acceptRisk`.

Those settings persist across Codex restarts. They do not need to be re-enabled in each thread.
A new thread is required after installing or updating the plugin so Codex loads its current tools,
skills, and hooks.

The repository also contains a Codex marketplace manifest for discovery:

```text
codex plugin marketplace add rladr-org/reviewer-agent
```

Use the checkout installer above for the complete local install and update flow.
For the minimal-human delegated route, use
[`INSTALL_WITH_AGENT.md`](INSTALL_WITH_AGENT.md) instead of running these commands manually.

## Update, disable and uninstall (Claude Code)

*Agents performing an unattended uninstall should follow
[`AGENT_UNINSTALL.md`](AGENT_UNINSTALL.md), which has per-step checks and the fail-closed
`bash_gate.py` caution below.*

**Update**:

```bash
claude plugin update reviewer-agent
```

Restart Claude Code afterwards so the reloaded hooks take effect.

**Disable per repository**, keeping everything installed — this is the usual thing to want:

```text
/reviewer-agent:reviewerAgent off
/reviewer-agent:stopContinue off
```

**Disable the plugin entirely** without removing it:

```bash
claude plugin disable reviewer-agent
```

**Uninstall**, in the order that leaves nothing dangling:

```bash
claude plugin uninstall reviewer-agent
claude plugin marketplace remove reviewer-agent      # optional: only if nothing else uses it
```

Then, optionally and in your own time:

- **Reviewer scratch (codex backend).** Session rotation and the `SessionStart` sweep normally
  reclaim it, but once the plugin is gone neither runs again, so leftover scratch persists and keeps
  using disk. With no other reviewer session active (a running audit owns a live scratch dir), delete
  the dedicated `reviewer-agent-scratch` directory under the system temp base (`$TMPDIR`, else `/tmp`).
  If you pointed `REVIEWER_SCRATCH_ROOT` at a directory of your own: remove it if it was dedicated to
  the reviewer; if it is shared, remove the reviewer's per-session subdirectories by hand — they are
  named after sanitized reviewer session ids and can't be told apart automatically from other contents.
- **State.** `rm -rf ~/.claude/reviewer-agent` removes every project's flags, reviewer session
  ids, retained verdicts and logs. To purge one project's review record instead, use
  `/reviewer-agent:reviewerHistory clear` before uninstalling. If you relocated state with `REVIEWER_STATE_DIR`
  (or the legacy `REVIEWER_HOME` alias), remove that path instead.
- **The Pi extension**, if you installed it: `rm -rf ~/.pi/agent/extensions/git-read-only`.
  Nothing else uses it.
- **The question timeout.** Enabling the reviewer set `askUserQuestionTimeout` in
  `~/.claude/settings.json`, because Claude Code only auto-closes an unanswered
  `AskUserQuestion` when that key is present. Uninstalling does not remove it: it is a host
  setting you may now want for other reasons. Delete the key by hand to restore the previous
  behaviour.
- **Legacy standalone hooks.** If you ever ran the pre-plugin install, its hook entries are
  still in `~/.claude/settings.json` and will keep firing after the plugin is gone. Remove the
  `SessionStart`, `Stop` and `PostToolUse` entries that point under `~/.claude/reviewerAgent/`, then
  delete `~/.claude/reviewerAgent/`. **One caution:** the standalone often registers a single
  `PreToolUse` (Bash) wrapper `~/.claude/hooks/bash_gate.py` that chains a general preflight hook —
  frequently your **own** bash-approval hook — and then the fail-closed reviewer `commit_hook.py`. Do
  not delete the directory while that wrapper still references `commit_hook.py`: it would then exec a
  missing script (fail-closed) on every Bash command and can block all of them. First edit
  `bash_gate.py` to drop the `commit_hook.py` link while keeping your preflight (or repoint the hook
  directly at your preflight script), then restart Claude Code and check `/hooks`. For an
  agent-driven teardown of any of this, see [`AGENT_UNINSTALL.md`](AGENT_UNINSTALL.md).

## Claude Code backends

The Claude Code plugin resolves one backend per repository, from three: `codex`, `pi` and
`claude`. Whichever it resolves handles all three Claude Code capabilities — commit audits,
timed-out question answering, and stop supervision. Nothing is selected for you beyond the
`codex` fallback, and `claude` in particular is never selected implicitly: it spends your own
subscription quota, so it is chosen explicitly or not at all.

### Codex backend

Install and authenticate `codex` with `codex login` or an API key.
Briefing requests a read-only sandbox. Commit audits request `workspace-write` with
`approval_policy="never"` so the reviewer can run tests; Git metadata is requested read-only to
prevent the reviewer from creating the commit. Timed-out question answers and stop supervision
request the read-only sandbox.

Set a nonstandard binary with `REVIEWER_CODEX_BIN`.

### Pi backend

Pi is model-agnostic. The plugin passes the configured model to `pi --model` and does not invoke
Codex as a fallback. Install and authenticate Pi against the desired provider before enabling the
reviewer.

The current commit-review invocation omits a shell and supplies Git access through the bundled
`git_read` extension, which constructs fixed arguments with `shell:false`. This design is intended
to keep the reviewer read-only and does not provide a way to run a test suite during an audit. Question answering
and stop supervision do not require a staged read, but `/reviewer-agent:reviewerAgent on` also enables the commit
gate, so a functional Pi installation must include the extension.

Pi prerequisites:

1. `pi` installed and authenticated;
2. Node 22.19 or newer;
3. `pi-extensions/git-read-only` installed under `~/.pi/agent/extensions/git-read-only/` with its
   dependencies installed by `npm ci`.

If Pi is installed through `nvm`, persist its absolute path as `piBin` in
`~/.claude/reviewer-agent/config.json`. Hook processes may not inherit the interactive shell’s
`PATH`. `REVIEWER_PI_BIN` has higher precedence but is also subject to environment inheritance.

Verify the installation:

```bash
command -v pi

node -e 'const [a,b]=process.versions.node.split(".").map(Number);
if (a<22 || (a===22 && b<19)) { console.error("Node 22.19+ required"); process.exit(1) }
console.log("node ok "+process.versions.node)'

node -e 'const fs=require("fs");
const p=process.env.HOME+"/.pi/agent/extensions/git-read-only/node_modules/";
const missing=["typebox","@earendil-works/pi-ai"].filter(m=>!fs.existsSync(p+m+"/package.json"));
if (missing.length) { console.error("missing: "+missing.join(", ")+" -- run npm ci"); process.exit(1) }
console.log("extension deps ok")'

node ~/.pi/agent/extensions/git-read-only/test/run.mjs
```

The commit gate checks for a successful staged `git_read` event. A missing extension, a tool
error, or a truncated staged diff overrides any model approval with a fail-closed rejection.

### Claude backend

The reviewer is a second `claude` process, so this backend installs nothing: it uses the CLI and
authentication already selected by the user. It is **opt-in and never the default**. Its usage,
quota, billing, availability and account consequences are controlled by Anthropic and the terms
applicable to the user's account, which may change.

> This software invokes third-party tools and services using credentials selected and controlled by
> the user. Provider terms, policies, availability, quotas, billing and enforcement are controlled
> by the provider and may change. The author makes no representation that any particular use is
> permitted under any provider plan. Users are responsible for reviewing the terms applicable to
> their accounts and obtaining any required authorization:
> [Usage Policy](https://www.anthropic.com/legal/aup) ·
> [Consumer Terms](https://www.anthropic.com/legal/consumer-terms) ·
> [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance).
>
> To the maximum extent permitted by law, no responsibility is accepted for provider actions or
> consequences, including charges, throttling, suspension, termination or loss of access. See
> [`DISCLAIMER.md`](DISCLAIMER.md).

Set a nonstandard binary with `REVIEWER_CLAUDE_BIN`. Choose the reviewing model with
`/reviewer-agent:reviewerModel <model>` and the depth with `/reviewer-agent:reviewerModel effort <level>`; levels are `low`,
`medium`, `high`, `xhigh` and `max`, defaulting to `medium`.

Three properties of the child process are deliberate, and each was established by running the CLI
rather than by reading its documentation:

1. **`--safe-mode` is mandatory.** Without it the child loads the operator's own hooks. In one
   measured run a child fired them 19 times: the `SessionStart` hook briefed a reviewer, the `Stop`
   hook then looped the child to its nudge cap, and the review returned empty. Safe mode disables
   hooks, plugins, MCP servers and `CLAUDE.md`, and unlike `--bare` it keeps subscription
   authentication working.
2. **`--permission-mode dontAsk`, not `auto`.** Under `auto` an allowlist is not exclusive:
   anything unnamed goes to the permission classifier, which was measured approving a file write,
   and the model located writers at runtime through `ToolSearch` and attempted to delegate through
   subagents. `dontAsk` denies everything not explicitly allowed, including tools nobody
   enumerated and tools a later CLI version introduces.
3. **No git access, by design.** Claude's `Read` returns the working tree, not the index, and
   granting a shell to reach the index would reintroduce the `diff.external` execution hole the Pi
   extension had to close. The staged patch is inlined into the prompt instead. A patch that
   exceeds the prompt budget (`REVIEWER_STAGED_PATCH_MAX`, 400000 bytes) blocks the commit rather
   than being truncated: a reviewer that saw half a diff must not be able to approve the whole of
   it.

Verify the installation:

```bash
command -v claude
claude --version
```

### Selecting and verifying a backend

Resolution order, highest precedence first:

1. `REVIEWER_BACKEND` environment variable;
2. per-project state override;
3. global state default;
4. `backend` in `config.json`;
5. `codex` fallback.

Set the global default through the plugin library so `REVIEWER_STATE_DIR` is honored:

```bash
python3 -c "import sys; sys.path.insert(0, '<PLUGIN_DIR>'); import reviewer_lib as rl; rl.set_default_backend('<codex-pi-or-claude>'); print(rl.backend_default_file())"
```

For one repository, use:

```python
rl.set_project_backend("<TARGET_REPO>", "pi")
```

Verify the resolved value rather than inspecting one configuration file, because a higher-priority
source may override it:

```bash
python3 -c "import sys; sys.path.insert(0, '<PLUGIN_DIR>'); import reviewer_lib as rl; print(rl.reviewer_backend('<TARGET_REPO>'))"
```

Valid values are `codex`, `pi` and `claude`. An invalid value is skipped and resolution continues
to the next source. Nothing selects `claude` for you: it spends your own subscription quota, so it
is chosen explicitly or not at all.

## Claude Code commands

All Claude Code plugin commands are invoked with the `/reviewer-agent:` prefix (Claude Code plugin
convention for namespaced commands).

| Command | Purpose |
|---|---|
| `/reviewer-agent:reviewerAgent on [acceptRisk]\|off` | Enable or disable commit review and timed-out question answering. First enablement requires the operator's exact `acceptRisk` invocation. |
| `/reviewer-agent:stopContinue on [acceptRisk]\|off` | Enable or disable stop supervision independently. The current risk receipt is shared with reviewerAgent. |
| `/reviewer-agent:stopContinueWatchdog on [interval]\|off\|status` | Arm/disarm a `CronCreate` recovery loop that re-invokes an idle session so the Stop hook can recover an unsupervised fail-open strand. Interval 1–59 min (default 7). Requires `stopContinue` active. The cron auto-shuts-off after a couple of idle intervals (so a finished session stops ticking within a few ticks rather than forever) and auto-rearms on your next prompt; the flag stays on across that. |
| `/reviewer-agent:reviewerDirective <text>` | Add a persistent fact or authorization to every reviewer prompt. Supports `list`, `remove <n>`, and `clear`. |
| `/reviewer-agent:reviewerModel` | Show the selected backend’s model, effort, and commit timeout. |
| `/reviewer-agent:reviewerModel <model>` | Set the model for the selected backend. |
| `/reviewer-agent:reviewerModel effort <level>` | Set reasoning effort for the selected backend. |
| `/reviewer-agent:reviewerModel timeout <seconds>` | Set the fail-closed commit-audit timeout. |
| `/reviewer-agent:reviewerModel firstOutputTimeout <seconds>\|clear` | codex backend only (not the pi/claude backends): first-output idle threshold (N1) for the streamed audit — how long to wait for the reviewer's first event before treating it as hung. Default 30 s (floor 5). |
| `/reviewer-agent:reviewerModel idleTimeout <seconds>\|clear` | codex backend only (not the pi/claude backends): between-output idle threshold (N2) for the streamed audit — the longest silent gap allowed between reviewer events; must exceed the longest legitimate silence (e.g. a test run). Default 900 s (floor 60). |
| `/reviewer-agent:reviewerSession` | Show reviewer and stop-supervisor sessions for the current Claude Code session. |
| `/reviewer-agent:reviewerSession rotate` | Retire all backend role sessions so the next calls receive the current brief. |
| `/reviewer-agent:reviewerSession limit <N\|off\|clear>` | Override, disable, or restore automatic rotation after completed non-retry audits. |
| `/reviewer-agent:reviewerHistory` | Show how many reviews this project has retained, where, and the retention cap. |
| `/reviewer-agent:reviewerHistory clear` | Delete this project's retained reviews and saved rulings. |

> These manual commands intentionally use Claude Code `!` shell preprocessing. Because command
> arguments are processed by the shell, avoid shell syntax and do not paste untrusted text into
> `/reviewer-agent:reviewerDirective`.

Codex reasoning levels are `none` through `ultra`. Pi levels are `off`, `minimal`, `low`,
`medium`, `high`, `xhigh`, and `max`. Claude levels are `low`, `medium`, `high`, `xhigh` and
`max`. Pi and Claude default to `medium`. The stop supervisor uses its separate
`REVIEWER_STOPCONTINUE_EFFORT` setting, defaulting to `low`.

## Prompt and session behavior

### Project brief and capsule

`reviewerAgent.md` is composed with a plugin-owned standing brief. The standing brief defines the
staged-set boundary, retry handling, required finding dispositions, and review output contract.
The project brief adds repository-specific requirements and takes precedence when more specific.

The full brief is sent once when each role session is created. A compact capsule containing the
non-negotiable rules, project-brief path, directives, and current context is sent on every commit,
question, and stop call. Changes to the capsule apply on the next call. Changes to the full brief
require `/reviewer-agent:reviewerSession rotate`.

The commit reviewer automatically rotates after three completed non-retry audits. Configure the
default with `reviewerSessionAuditLimit` in `~/.claude/reviewer-agent/config.json` (`0` disables
it), or override it live with `/reviewer-agent:reviewerSession limit <N|off|clear>`. A retry is any commit attempt
after a rejection until the next approval; retries never advance the rotation counter. A
first-pass rejection itself counts once; only the later attempts in its retry chain are excluded.
Automatic rotation leaves the separate stop-supervisor session untouched.

### Role isolation

Within one Claude Code session, every backend keeps separate role sessions:

- the reviewer session handles commit audits and timed-out questions;
- the stop session handles `stopContinue` only.

For Codex these are stored as `sess_<claude-session>.reviewer.codex` and
`sess_<claude-session>.stop.codex`. For Pi they are `sess_<claude-session>.pi` and
`sess_<claude-session>.stop.pi`. For Claude they are `sess_<claude-session>.claude` and
`sess_<claude-session>.stop.claude`.

The split prevents frequent stop-supervision turns from contaminating later commit audits. A stop
supervisor’s directive does not pre-approve a later commit; the commit reviewer is an independent
role session.

Codex reviewer sessions are normally created at Claude Code `SessionStart`. Pi and Claude sessions
are created lazily on the first call for each role. Selecting either causes `SessionStart` to skip
Codex entirely.

The Claude backend differs in one detail: it does not learn its session identifier from the
child's output, it assigns one. The hook mints a UUID, passes it as `--session-id`, records it
only after a turn the CLI actually accepted, and resumes it by name thereafter.

Session identifiers are captured and resumed explicitly. The implementation never uses
`codex resume --last`, which could attach to another Claude Code session or a manual Codex run.

### Re-attempted commits

After a rejection, the next audit receives a staged-path table marking each path `CHANGED`,
`ADDED`, `UNCHANGED`, or `REMOVED` relative to the exact bytes previously reviewed. Diffs for
changed paths are inlined from the Git index, so unstaged edits cannot be cited as part of the
commit.

Inlining is bounded by `deltaInlineMax` (48,000 bytes per path) and `deltaInlineTotalMax` (96,000
bytes total). Either may be set to `0` to disable inlining. Withheld content is explicitly marked.

Paths matched by `reviewerAgent-ignore.md` are dropped from this retry inline set on the codex
backend (Channel B), so an auto-generated data file's diff bytes do not consume retry-prompt
tokens. Codex retains full fetch authority — it can still run `git diff --cached -- <path>`
itself if a matched change looks suspicious. See the "Skimmed paths" section below.

### Skimmed paths (`reviewerAgent-ignore.md`)

A per-repository `reviewerAgent-ignore.md` at the repo root can name paths whose content is
auto-generated data (dataset rows, captured HTML, generated manifests) so the reviewer skims
their diffs instead of judging individual records. The file is optional — a missing file is
today's baseline, no exclusions.

Format: free-form markdown with one or more fenced blocks tagged ```reviewer-ignore``` whose
lines are glob patterns:

    ```reviewer-ignore
    data/**/*.jsonl
    sources/*.txt
    **/*.pdf
    ```

Supported globs: `*` (within one segment), `**` (zero-or-more full segments, only as
leading/trailing/standalone form), `?`, `[abc]`/`[a-z]`, leading `/` for repo-root anchoring,
`#` comments. Explicitly not supported (fail-closed): `!` negation, backslash escapes,
embedded `**` like `**foo`, negated character classes.

Two delivery channels:
- **Channel A** — the file's full markdown (prose + fences) is appended to the per-commit
  audit prompt in a labelled block, so the reviewer reads the operator's rationale
  alongside the patterns. Backend-specific follow-up guidance is included: codex is told
  to fetch on demand with `git diff --cached`; pi via its `git_read` tool; claude via the
  inlined patch already in the message.
- **Channel B** — on retries, codex drops matched paths from the delta candidate set so
  their diff bytes do not reach the retry prompt (pi and claude are Channel A only in v1).

Security rules the parser enforces:
- The file is read from git objects (index or HEAD), never the working tree. An unstaged
  or untracked working-tree copy has no effect.
- Delayed application: when the ignore file itself is one of the paths being changed by
  this commit, the audit uses the **HEAD** version (patterns and prose) — the staged
  changes to the ignore file only take effect on the *next* commit. This guards against
  a trojan commit that simultaneously loosens the ignore rules and hides malicious
  changes under the newly-permitted paths.
- Self-exemption: no pattern (not even `**`) can hide the root
  `reviewerAgent-ignore.md`'s own diff from review.
- Staged validation: parse errors in the staged version fail the commit closed, even
  when the effective source is HEAD or absent — so an operator learns at commit time
  that their new content is malformed, not on the next commit.
- Fail-closed on parse error: any unsupported syntax blocks the commit BEFORE any
  backend runs, with the pattern and line number surfaced in the operator-visible
  reason.

### Verdict retention

Both host packages keep two copies of commit-review results outside the reviewed repository: a
stable latest-verdict file used for retry context and a chronological per-project history whose
entries are never overwritten. The history spans developer sessions and records approvals and
rejections.

It also records the outcomes that carry no verdict -- a reviewer timeout, a cancelled review, an
operational failure, an empty response -- as labelled `OUTCOME:` entries, because each of those
blocked a commit and is a real event an operator later asks about. None of them touches the
latest-verdict file: that file is shown to the next reviewer as its own previous findings, so a
timeout must never overwrite the last real ruling.

The newest 100 history ENTRIES per project are retained by default, with oldest-first pruning.
Verdicts and `OUTCOME:` records share one history and one cap, so a run of timeouts prunes older
verdicts exactly as further verdicts would. Both packages accept `reviewHistoryKeep` in their
persistent `config.json`. The Claude Code package uses `REVIEWER_REVIEW_HISTORY_KEEP` as its
environment override; the Codex package uses `REVIEWER_CODEX_REVIEW_HISTORY_KEEP`. Each setting is
read when an entry is written, so changing it does not require reinstalling the plugin.

Retained verdicts quote the staged diffs they judged, so the history is a plaintext record of
reviewed code as well as of reviews. `/reviewer-agent:reviewerHistory` reports what is retained for the current
project and `/reviewer-agent:reviewerHistory clear` deletes it -- retained reviews and saved rulings, for that
project only -- without uninstalling anything. The Codex package exposes the same two actions as
`$reviewer-agent-codex:reviewer-history status|clear`.

### Background work and stop supervision

The stop hook allows a turn to end without a supervisor call when the transcript shows a live
harness-managed background shell, subagent, or monitor. The host is expected to resume the session
when that work completes. Staleness windows prevent abandoned markers from suppressing supervision
indefinitely.

Every stop-hook invocation writes a `[stop] ENTER` record to
`~/.claude/reviewer-agent/logs/reviewer.log`. If an expected stop was not supervised:

```bash
grep "\[stop\] ENTER" ~/.claude/reviewer-agent/logs/reviewer.log
grep "\[stop\] EXCEPTION" ~/.claude/reviewer-agent/logs/reviewer.log
```

No matching `ENTER` usually means the Claude Code session has a stale hook snapshot; restart or
refresh the session. An `ENTER` line records the flag, final-text length, nudge state, and detected
background wait.

## Expected failure modes and troubleshooting

Most first-run problems are one of these six.

The remedies differ by host, because the two packages have separate controls. Claude Code
commands are slash commands; the Codex package is driven by namespaced invocations.

**Every commit is blocked and the reviewer never seems to run.** The configured model or
reasoning effort is not reachable on your account. The gate is fail-closed, so this presents as
a permanent block rather than an error. Since 1.2.0-beta.1 the message names the selection, its
source and the control that changes it. An environment variable beats the stored setting, and
the diagnostic says so when one is in play.

| | Show the selection | Change it |
|---|---|---|
| Claude Code | `/reviewer-agent:reviewerModel` | `/reviewer-agent:reviewerModel <model>`, `/reviewer-agent:reviewerModel effort <level>` |
| Codex | `$reviewer-agent-codex:reviewer-model` | `$reviewer-agent-codex:reviewer-model <model> [<effort>]` |

**A commit is blocked with "TIMED OUT".** A timeout carries no verdict, so it cannot approve.
Retry the identical commit first; if the diff is genuinely too large for the budget, raise it.
The two hosts recover differently:

- **Claude Code** resumes the reviewer session by id, so a retry keeps what the reviewer already
  read and often lands second time. Raise the budget with `/reviewer-agent:reviewerModel timeout <seconds>`
  (default 900s).
- **Codex** *retires* the timed-out reviewer automatically and says so in the tool result: the
  retry starts a fresh reviewer, and rotating by hand afterwards is unnecessary. Raise the budget
  with `REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS`, or `commitReviewTimeoutSeconds` in
  `~/.codex/reviewer-agent-codex/config.json`. Accepted values are whole seconds from 60 to
  7200; anything outside that range, or not an integer, is *skipped* rather than clamped, and
  resolution falls through to the next source and finally to the 3600s default. So a typo does
  not silently give you a one-second budget -- but it also does not give you the number you
  typed, and the only sign is that the timeout is still the default.

**Every commit is blocked and the message says a usage limit was reached.** Only the `claude`
backend can produce this: it runs on your own Claude subscription quota, and the commit gate is
fail-closed, so an exhausted allowance blocks commits until it resets. Changing the model does not
help. Wait and retry the identical commit -- the reviewer session resumes by id, so it keeps what
it had already read -- or, to keep working meanwhile, switch this repository to another backend
(`REVIEWER_BACKEND`, or `backend` in `config.json`) or run `/reviewer-agent:reviewerAgent off`.

**A commit is blocked with "EMPTY verdict".** The reviewer session is wedged; retiring it makes
the next gated action brief a fresh one, at the cost of one cold start. In Claude Code that is
`/reviewer-agent:reviewerSession rotate`; in Codex it is `$reviewer-agent-codex:reviewer-session rotate`.

**The reviewer answers as if it were reviewing a different repository.** Current state files are
keyed by project as well as by session to prevent the previously observed collision. If this
occurs, disable the reviewer and report it; do not rely on the isolation claim alone.

**`AskUserQuestion` is never answered for you.** That path only fires when Claude Code
auto-closes an *unanswered* question, which it only does when `askUserQuestionTimeout` is set in
`~/.claude/settings.json`. A successful reviewerAgent enable arms it; if that step warned, the key
is missing.

Where to look when it is none of those: `~/.claude/reviewer-agent/logs/reviewer.log` has one
line per gated action, with the exit code, the verdict tail and the stderr tail. The Codex
package writes no log; its `review_commit` result carries the same detail, and
`$reviewer-agent-codex:reviewer-history status` finds the retained verdicts. Please report what
you find -- see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Claude Code configuration

These settings apply to the repository-root Claude Code plugin, not the Codex package.

| Variable | Default | Purpose |
|---|---|---|
| `REVIEWER_STATE_DIR` | `~/.claude/reviewer-agent` | Flags, session IDs, logs, reviews, and configuration. |
| `REVIEWER_HOME` | — | Legacy alias for `REVIEWER_STATE_DIR`. |
| `REVIEWER_BACKEND` | `codex` fallback | One-call/process override: `codex`, `pi` or `claude`. |
| `REVIEWER_CODEX_BIN` | `~/.local/bin/codex` | Codex executable override. |
| `REVIEWER_PI_BIN` | `pi` on `PATH` | Pi executable override; `piBin` in `config.json` is more reliable for hooks. |
| `REVIEWER_PI_MODEL` | Pi default | Value passed to `pi --model`. |
| `REVIEWER_PI_EFFORT` | `medium` | Value passed to `pi --thinking`. |
| `REVIEWER_CLAUDE_BIN` | `claude` on `PATH` | Claude executable override. |
| `REVIEWER_CLAUDE_MODEL` | CLI default | Value passed to `claude --model`. |
| `REVIEWER_CLAUDE_EFFORT` | `medium` | Value passed to `claude --effort`. |
| `REVIEWER_STAGED_PATCH_MAX` | `400000` | Bytes of staged patch the `claude` backend will inline into a prompt. A larger patch blocks the commit rather than being truncated. |
| `REVIEWER_DELTA_INLINE_MAX` | `48000` | Re-attempt diff bytes inlined per path; `0` disables. |
| `REVIEWER_DELTA_INLINE_TOTAL_MAX` | `96000` | Re-attempt diff bytes inlined in total; `0` disables. |
| `REVIEWER_STOPCONTINUE_EFFORT` | `low` | Stop-supervisor reasoning effort. |
| `REVIEWER_STOPCONTINUE_MAX_NUDGES` | `10` | Maximum forced continuations per user turn. |
| `REVIEWER_REVIEW_HISTORY_KEEP` | `100` | History entries retained per project (verdicts and `OUTCOME:` records share the cap). |
| `REVIEWER_ASYNC_AGENT_STALE_SECS` | `14400` | Live subagent marker window. |
| `REVIEWER_BG_SHELL_STALE_SECS` | `43200` | Live background-shell marker window. |
| `REVIEWER_MONITOR_STALE_SECS` | `3600` | Live monitor marker window. |
| `REVIEWER_COMMIT_TIMEOUT_SECS` | `900` | Commit-audit timeout. |
| `REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS` | `30` (floor 5) | codex backend only (not the pi/claude backends): first-output idle threshold (N1) for the streamed audit. Also `firstOutputTimeoutSecs` in `config.json`. |
| `REVIEWER_IDLE_TIMEOUT_SECS` | `900` (floor 60) | codex backend only (not the pi/claude backends): between-output idle threshold (N2) for the streamed audit; must exceed the longest legitimate silence. Also `idleTimeoutSecs` in `config.json`. |
| `REVIEWER_SCRATCH_ROOT` | `<system temp>/reviewer-agent-scratch` | Codex backend: root for per-reviewer-session scratch dirs. `TMPDIR` is scoped to one on each codex audit; the dir is reclaimed at rotation and by a lease sweep. |
| `REVIEWER_SCRATCH_TTL_S` | `43200` (12 h) | Codex backend: orphan-sweep lease — at SessionStart, scratch dirs whose mtime is older than this are reclaimed (covers sessions that ended without rotating). |
| `REVIEWER_DISK_WARN_BYTES` | `2147483648` (2 GiB) | Codex backend: warn-only free-space threshold; below it the audit prints a low-disk warning and never blocks the commit. `0` disables. |

The commit timeout is shared by all three Claude Code backends. A timeout blocks the commit.
The question and stop paths each use a separate 300-second limit and fail open.

The streaming, scratch, and idle-threshold behavior below is specific to **this Claude Code plugin
running its `codex` reviewer backend** (Claude Code as the host, codex as the reviewer). It does not
apply to this plugin's `pi` or `claude` backends, and it is unrelated to the separate native-Codex
host plugin (`plugins/reviewer-agent-codex/`), which has its own review implementation.

With the codex backend, the commit audit is streamed and schema-constrained: it runs `codex exec …
resume --json --output-schema`, so the reviewer returns a strict JSON verdict that the hook
reconstructs into the canonical `APPROVED`/`REJECTED` text. The `--json` event stream is watched
against the two idle thresholds above; a hung reviewer is killed and retried while the audit's
total budget remains and the retry count is below `MAX_IDLE_RETRIES` (8), and a missing or
malformed verdict fails closed (blocking the commit, preserving any saved ruling) rather than passing.
Each such audit also runs with `TMPDIR` scoped to a per-reviewer-generation scratch dir under
`REVIEWER_SCRATCH_ROOT`, which is removed when the reviewer session rotates and otherwise reclaimed by
the SessionStart lease sweep. This contains and reclaims scratch that honors `$TMPDIR`; it does **not**
reach scratch the reviewer or its tooling writes to a hardcoded absolute path outside `$TMPDIR` (for
example under `/tmp` directly), which is not under `REVIEWER_SCRATCH_ROOT` and is not reclaimed. The
audit prompt asks the reviewer to keep its scratch under `$TMPDIR` to mitigate that, and the low-disk
warning is the backstop for the rest.

## Host versions it has been run against

| Component | Minimum | Why this number |
|---|---|---|
| Claude Code | 2.1.238 | The oldest version the 1.2.0-beta.1 release matrix was run against. |
| Codex CLI | 0.147.0 | The oldest version the 1.2.0-beta.1 release matrix was run against. |
| Node | 22.19 | Required by Pi itself; only needed for the optional Pi backend. |
| Python | 3.9 | The macOS system interpreter at `/usr/bin/python3`. |

These are the oldest versions this release was actually *exercised* on, not a minimum anyone
undertakes to support, and not the oldest that can work. Older hosts are a caveat rather than a
refusal: a successful `/reviewer-agent:reviewerAgent on` and the Codex installer both detect the host version and print a
warning below these floors, then continue. Newer versions are equally untested — a host that
changes underneath this project can break it at any time, and nothing here promises otherwise.

Python 3.9 is past upstream end of life and is kept deliberately. macOS ships `/usr/bin/python3`
at 3.9, and this package is standard-library-only at runtime precisely so it installs against the
system interpreter without anyone having to install Python first. Development dependencies
(`requirements-dev.txt`) are a separate matter and are only needed to run the test suites.

## Platforms it has been run on

**No platform is supported.** What differs between them is only how much evidence exists, and on
every one of them the answer is "not much".

- **macOS Tahoe** — the only platform where this has been installed and used. The release matrix
  (install, reject, approve, cancel, stop, uninstall) is run there by hand, and two configurations
  have had daily use. That is evidence it can work on one machine, not assurance it will work on
  yours.
- **Other macOS versions** — never tried.
- **Linux** — CI unit tests only (Ubuntu, Python 3.9/3.12/3.14). No installation has ever been
  performed on a Linux host. Automated coverage is not evidence that installing it works.
- **WSL2** — no CI, no manual run, nothing.
- **Native Windows and WSL1** — the code is not written for them. The Claude Code package's
  Bash-oriented commands and both packages' POSIX process control have not been ported to native
  Windows. Claude Code can run in WSL1, although its sandbox is unavailable there, but current
  Codex cannot: from [Codex 0.115](https://learn.chatgpt.com/docs/windows/wsl) its Linux sandbox
  uses `bubblewrap`, which WSL1 does not provide. A Claude Code plus Pi installation might work in
  WSL1; nobody has tried it.

Linux and WSL2 are expected to work, because the runtime and process control use POSIX
interfaces — but expected is not the same as tried, and this section exists to keep those apart.
The host's own platform coverage says nothing about whether this project has ever been run there.

### Native Linux (never run there)

These directions apply to both hosts. Install Git, `curl`, and Python 3.9 or newer with the Linux
distribution's package manager, and confirm that the interpreter is named `python3`:

```bash
git --version
curl --version
python3 -c 'import sys; assert sys.version_info >= (3, 9); print(sys.version)'
```

Install only the developer host you intend to use. The
[Claude Code installation guide](https://code.claude.com/docs/en/installation) and
[OpenAI Codex WSL guide](https://learn.chatgpt.com/docs/windows/wsl) provide these Linux installers:

```bash
# Claude Code host
curl -fsSL https://claude.ai/install.sh | bash

# Codex host
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

Authenticate and verify the selected host with `claude --version` or `codex --version`. Then follow
[Claude Code installation](#claude-code-installation) for the Claude Code plugin or
[Codex installation](#codex-installation) for the Codex plugin. A Claude Code installation using
the Pi backend does not need Codex; install and authenticate Pi as described under
[Pi backend](#pi-backend).

### Windows through WSL2 (never run there)

First install WSL2 from an elevated PowerShell window, then restart Windows if prompted. See the
[OpenAI WSL guide](https://learn.chatgpt.com/docs/windows/wsl) for host setup and troubleshooting:

```powershell
wsl --install
wsl --list --verbose
```

In the listing, the selected distribution's `VERSION` column must show `2`. If it shows `1`, back
up important distribution data and convert that distribution before continuing (replace the
placeholder with the name shown by the listing):

```powershell
wsl --set-version <distribution-name> 2
```

Open the verified WSL2 distribution. Run every remaining command—including the Claude Code or
Codex host, this repository's installer, and the protected project—inside that WSL2 terminal, not
in PowerShell or CMD. Confirm both the environment and its WSL generation, then verify Python:

```bash
echo "$WSL_DISTRO_NAME"
uname -r | grep -qi 'WSL2' || { echo 'WSL2 not detected'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 9); print(sys.version)'
```

Keep the plugin checkout and protected repositories in the WSL Linux filesystem, such as
`~/code`, rather than under a mounted Windows path such as `/mnt/c`. OpenAI recommends the Linux
filesystem for better performance and more reliable permissions and symbolic links:

```bash
mkdir -p ~/code
cd ~/code
git clone https://github.com/rladr-org/reviewer-agent.git
```

Install the selected host inside WSL2 with the corresponding Linux command in the previous section,
then follow its normal plugin instructions in this README. Do not install a Windows-hosted copy and
invoke it across the WSL boundary.

### Report Linux and WSL2 bugs

Please report failures at [GitHub Issues](https://github.com/rladr-org/reviewer-agent/issues). A
useful report names the distribution and version, native Linux or WSL2, Claude Code or Codex and its
version, Python version, reviewer backend, the failed command or hook, and the complete error after
removing credentials or private repository content. Reports may be read and acted upon, or may
not — see [`CONTRIBUTING.md`](CONTRIBUTING.md). Filing one is worth doing anyway: it is the only
way a platform this has never run on could ever get evidence behind it.

The Codex and Pi backends are external dependencies and neither is bundled; the Claude backend runs the `claude` CLI you already have, on your own subscription quota.

## Repository layout

```text
.agents/plugins/marketplace.json       Codex marketplace entry
.claude-plugin/                        Claude Code plugin metadata
plugins/reviewer-agent-codex/          self-contained Codex plugin and guide
hooks/hooks.json                       Claude Code hook registration
skills/<name>/SKILL.md                 Claude Code slash commands (invoked as /reviewer-agent:<name>)
reviewer_*.py                          Claude Code runtime layers
commit_hook.py                         Claude Code commit gate
askq_answer_hook.py                    Claude Code timed-out question handler
stop_hook.py                           Claude Code stop supervisor
session_start_hook.py                  Claude Code Codex-session bootstrap
reviewerAgent.md.template              project brief template
pi-extensions/git-read-only/           required read-only Pi Git extension
tests/                                 Claude Code and distribution tests
```

## Tests

The two suites are independent and each has one canonical command. Run each from its own
package root; the root `pytest.ini` keeps a bare root run out of the nested Codex suite,
whose modules import `reviewer_codex` relative to the plugin directory.

```bash
python3 -m pytest -p no:cacheprovider                     # from the repository root
cd plugins/reviewer-agent-codex && python3 -m pytest -p no:cacheprovider
```

Development dependencies live in `requirements-dev.txt` (`pip install -r
requirements-dev.txt`). The installed runtime itself is standard-library-only.

Run structural checks for the Claude Code module split:

```bash
python3 tools/check_split_layering.py
python3 tools/gen_facade_contract.py
```

Run the Pi extension tests:

```bash
node pi-extensions/git-read-only/test/run.mjs
PI_GIT_READ_LIVE=1 node pi-extensions/git-read-only/test/live.mjs
```

`PI_GIT_READ_LIVE=1` is required; without it the live test file skips its checks.

## Backlog

- Add Linux and WSL2 CI plus live-host coverage before claiming either platform has been run; treat
  native Windows process control as a separate portability project.
- Add an explicit opt-in Pi shell mode if Pi gains a permission model that can prevent writes and
  shell redirection. Pi remains shell-free by default.
- Persist a nonstandard Codex path in `config.json` as `codexBin`, parallel to `piBin`.
- Add a Pi backend to the Codex package. This requires a Codex-hosted blocking interface that does
  not assume the Pi user has installed or authenticated Codex.

## License

Mozilla Public License 2.0 — see [`LICENSE`](LICENSE). The Codex plugin carries its own copy at
[`plugins/reviewer-agent-codex/LICENSE`](plugins/reviewer-agent-codex/LICENSE).

Source files carry `SPDX-License-Identifier: MPL-2.0` rather than a repeated Exhibit A block.
MPL 2.0 Exhibit A permits the notice to live "in a location (such as a LICENSE file in a
relevant directory) where a recipient would be likely to look for such a notice" when putting it
in a particular file is not desirable; the two `LICENSE` files above are that location, and they
carry the unmodified license text. JSON, Markdown, lockfiles and generated files carry no header
and rely on the same `LICENSE`.

Copyright (c) 2026 RLADR.

Claude, Claude Code, Anthropic, Codex, OpenAI and Pi are third-party names used only to identify
interoperability. No affiliation with or endorsement by their respective owners is claimed.
