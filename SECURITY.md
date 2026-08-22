# Security

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. That channel is monitored; public issues are not the
right place for a vulnerability, because this project runs inside a developer's shell with
access to their working tree.

Please include the host, the backend, the plugin version, the model, and the smallest
reproduction you have:

| | Claude Code | Codex |
|---|---|---|
| Plugin version | `.claude-plugin/plugin.json` | `plugins/reviewer-agent-codex/.codex-plugin/plugin.json` |
| Model and effort | `/reviewerModel` | `$reviewer-agent-codex:reviewer-model` |
| Backend | Codex, Pi or Claude (`/reviewerModel` reports the selection) | always Codex |

This is a beta maintained by one person, so expect a first response in days rather than hours.

## What this project is, in security terms

`reviewer-agent` inserts a second AI agent between you and your commits. That agent reads your
staged changes and, on the Codex backend, **runs commands in your repository**. Everything below
follows from those two facts.

## Threat model

**Repository content is attacker-controlled input.** Any branch you pull, any dependency you
vendor, any patch you apply from a stranger becomes reviewer input. The reviewer reads it with a
model, and on the Codex backend may execute the repository's own test suite to judge it. A
malicious repository is therefore a code-execution vector against the machine running the
review, exactly as running that repository's tests by hand would be — the difference is that the
reviewer does it automatically, when you commit, without a separate prompt.

**Hostile content shares a context window with your instructions.** The reviewer's prompt
contains the staged diff alongside your project brief (`reviewerAgent.md`), your standing
directives (`/reviewerDirective`), your typed messages from the session transcript, and — on
native Codex — the current plan and task list. A diff crafted to look like an instruction is a
prompt-injection attempt against the reviewer. The reviewer's output is not executed, and the
gate treats anything that is not an explicit approval as a rejection, so the realistic damage is
a wrong verdict (a bad change approved, or a good change blocked) rather than direct control of
your machine. On the Codex backend it can also influence *which commands the reviewer chooses to
run* while auditing.

**The gate is fail-closed for commits and fail-open elsewhere.** A failed, timed-out or
cancelled commit review blocks the commit. A failed question answer or stop supervision allows
the turn to proceed. That asymmetry is deliberate: an unreviewed commit is the outcome worth
preventing, and a blocked agent is not.

## Execution boundary, per backend

The three backends are not equivalent, and the difference is the main thing to understand before
choosing one.

### Codex backend — executes commands

| Turn | Sandbox | Approvals |
|---|---|---|
| Briefing (session creation) | `read-only` | disabled |
| Commit audit | `workspace-write` | disabled (`approval_policy="never"`) |
| Stop supervision | `read-only` | disabled |
| Timed-out question answer | `read-only` | disabled |

A commit audit runs with the working tree and the temporary directory writable, deliberately, so
the reviewer can run your tests before ruling on a change. `.git` is not writable, so the
reviewer structurally cannot create a commit — approval is relayed back and *your* host performs
the commit. Approvals are disabled because there is no human in that loop to answer them.

Reviewer-directed test execution is real command execution against your working tree, under
whatever sandbox the Codex CLI provides on your platform. Treat "I enabled the Codex reviewer" as
"my test suite may run automatically when I commit".

What bounds it:

- **Timeout.** Configurable, defaulting to 900s (Claude Code) and 3600s (native Codex); a
  timeout kills the review and blocks the commit.
- **Cancellation.** Escape terminates the review and every process it started, including
  processes that created their own process groups.
- **Process-tree cleanup.** A reviewer that dies, times out, or outlives its hook does not leave
  test processes running: descendants are enumerated from the kernel and terminated as a tree,
  and a guard process kills the tree if the hook itself is killed outright.
- **No plugins.** Every reviewer session runs with `features.plugins=false`, so a reviewer
  cannot load this plugin -- or any other -- inside its own Codex session, and cannot re-enter
  the gate it exists to serve. The native-Codex package additionally sets
  `features.hooks=false` on its child reviewers, because there the parent's own hooks would
  otherwise fire on the child's turns; the Claude Code package does not set that flag, so a
  hook you configured directly in `~/.codex/config.toml` still runs inside a reviewer session
  started from Claude Code.

### Pi backend — no shell

The Pi reviewer has no shell, no write tool and no edit tool. Its tool allowlist (`-t`) contains
read and search built-ins plus one custom tool, `git_read`: a tested, no-shell, read-only git
wrapper. A Pi review is a static audit of the staged patch.

The read-only guarantee rests on that allowlist and on the extension being present. If the
`git-read-only` extension is missing, the review fails loudly rather than proceeding without it,
and a review that never successfully read the staged patch is converted into a rejection rather
than an approval.

Selecting Pi does not install, launch, or fall back to Codex.

### Claude backend — no shell, and no hooks

The Claude reviewer is a `claude` child process. Its tool allowlist is `Read`, `Grep` and `Glob`;
`Bash`, `Write`, `Edit`, `NotebookEdit`, `WebFetch` and `WebSearch` are denied by name as well.
It has no git access of any kind, so the staged patch is included in its prompt rather than
fetched, and a patch too large to include blocks the commit instead of being truncated.

Two flags carry that guarantee, and both were chosen from measured behaviour rather than from
documentation:

- **`--safe-mode`.** Without it the child loads the operator's own hooks, plugins, MCP servers
  and `CLAUDE.md`. In one measured run a child fired the operator's hooks 19 times — the
  `SessionStart` hook briefed a reviewer inside the reviewer, the `Stop` hook then looped the
  child to its nudge cap, and the review returned empty. Safe mode is what keeps a reviewer from
  re-entering the machinery it exists to serve. Unlike `--bare` it leaves subscription
  authentication working.
- **`--permission-mode dontAsk`.** Not `auto`: under `auto` the allowlist is not exclusive, so an
  unnamed tool is referred to the permission classifier. That classifier was measured approving a
  file write, and the model reached for `ToolSearch` and subagents to find a writer the allowlist
  had not named. `dontAsk` denies everything not explicitly allowed — including tools nobody
  enumerated and tools a later CLI version introduces — and held across repeated attempts by a
  model actively trying to escape it.

This package reads, copies and forwards no credential: the child authenticates itself exactly as
the operator's own CLI does. Because it does, the review is billed to the operator's own Claude
allowance, and an exhausted allowance blocks commits until it resets (fail-closed). Selecting
Claude does not install, launch, or fall back to Codex.

## Trust boundaries

- **The reviewer is not a security control.** It is a second opinion that blocks commits. It
  will miss things, and it can be argued out of findings by a sufficiently confident diff. Do
  not use it as your only review of untrusted code.
- **Providers see your code.** See [`PRIVACY.md`](PRIVACY.md) for exactly what is sent where.
- **Local state is plaintext.** Retained verdicts quote the diffs they judged. See
  [`PRIVACY.md`](PRIVACY.md) for locations and how to purge them.
- **Hooks run with your privileges.** Both packages install hooks that run on your commits and
  turn ends, as the user running the host. Install from a source you trust and read
  `AGENT_INSTALL.md` before letting an agent install it for you.

## Reporting scope

In scope: sandbox escapes, a path by which reviewer output executes on the host, state written
outside the documented directories, one project's reviewer reading another project's state,
credentials or repository content written to logs, and any way to make the commit gate approve
without a reviewer verdict.

Out of scope: the reviewer model being wrong, the provider's own security posture, and prompt
injection that produces a wrong verdict without crossing one of the boundaries above (it is a
known and documented property, not a defect).
