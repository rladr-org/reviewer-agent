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
Clone https://github.com/rladr-org/reviewer-agent.git into a new permanent user-owned directory outside this target repository if needed, then read `INSTALL_WITH_AGENT.md` completely and follow it to install reviewer-agent for this repository. Use the current developer host. Resolve only installation prerequisites early. After installation, hook trust where applicable, and host restart are verified, follow the settings walkthrough to ask me which features, reviewer backend/model, reasoning effort and timeouts to use, showing the effective settings and defaults. Explain the separate operator risk-acceptance step. Otherwise perform and verify every step you are authorized to do.
```

If a checkout already exists, replace the clone clause with its absolute path. During installation,
the human must approve writes outside the repository when the host requests approval. After
installation and before settings confirmation, restart the developer host. For Codex, hook trust
must be completed in the
interactive CLI even when the human uses the GUI: run `codex` in a terminal, enter `/hooks`, and
inspect the `reviewer-agent-codex` hooks. Trust only entries requiring approval; already-trusted
entries complete this check without another enable action. Then fully restart the GUI, if that is the surface being
used. Those actions cannot safely be delegated.

## Procedure for the installing agent

Read this entire file before acting. Do not ask the human to run a command that the agent can run,
except that only the human may make the exact `acceptRisk` invocation described below.
Use the host's normal approval mechanism for external writes, and stop on a failed verification
rather than declaring a partial fail-closed gate installed.

### Reinstall entry point

For a requested uninstall/reinstall test, first finish the applicable
[uninstall procedure](AGENT_UNINSTALL.md), including verification in a fresh host. Then follow this
procedure from step 1; do not collapse it into an update or a temporary wrapper around the two
scripts. When evaluating these instructions, use their actual commands and record each exit status
and check result. If a step is missing or incorrect, report and correct the documentation before
proceeding. A successful installer does not erase an earlier failed uninstall check.

For a fresh walkthrough of this guide, ask the settings questions again; do not substitute answers
from the update walkthrough. Show the preserved effective values and sources rather than assuming
factory defaults. Confirming them does not accept risk or enable a feature. Preserve existing
receipts and feature state; no repeat acceptance is needed when a receipt remains current.
Keep clean-host installation, first-time repository setup and reinstall coverage distinct.

Resume at the first unfinished numbered step, verifying its prerequisites from observed results.
Do not jump to settings confirmation because the user asks to restart the walkthrough. If settings
were presented before installation, hook trust and host restart were verified, explain the ordering
error and complete those prerequisites first. Then inspect the loaded effective configuration and
ask the settings question again at step 5. A premature answer is not completion of that checkpoint;
do not skip it or advance directly to enablement using that answer. When testing this procedure,
correct any conflicting linked instructions before continuing and record the failed walkthrough
step separately from successful installer or host checks.

### Continue across operator checkpoints

Keep a short installation checkpoint in the existing handoff: target, source/version, completed
checks and their evidence, the pending operator action, and the next numbered step or settings
screen. Update it before yielding for a restart or operator-only command. Do not create a new
handoff format when one already exists.

Treat a natural-language report such as “restarted,” “updated it,” “hooks were already enabled,”
or “done” as a request to resume the unfinished installation. The literal word `continue` is
not required after restart/trust; after a control command it is a convenient separate message,
not the only wording that can resume setup. Verify the reported checkpoint, then perform the next
authorized step or ask the next required settings question in the same turn. Do not end with only
an acknowledgement or status report. Questions and feedback during setup do not cancel it:
answer them briefly, retain the checkpoint, and continue unless the operator explicitly pauses
or replaces the task. A restart report does not confirm settings or select feature enablement.

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

### 2. Inspect changes and prerequisites

Resolve only host/backend prerequisites needed to install at this stage (including the operator's
Claude backend choice if applicable). The feature settings/enablement walkthrough happens after
installation and reload in step 5.

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
permanent. For Claude Code, ensure `python3` is Python 3.9+ on the developer host's launch PATH:
activating a test environment in one shell does not configure the hooks of a running GUI or
another shell. For Codex, run the installer with a permanent Python 3.9+ installation, not the
temporary test environment. The installer verifies and saves that interpreter for both hooks
and MCP, so Codex does not need the installer shell's PATH. Keep the selected interpreter
installed; rerun the installer if it moves or is removed.

### 3A. Claude Code installation

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

### 3B. Codex installation

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

### 4. Hook trust and host restart

After the installation checks pass, complete hook trust and host restart before starting the
feature walkthrough. Explain that hook trust permits execution; it is not risk acceptance and
does not enable a previously disabled feature. Name `<TARGET_REPO>` and report retained feature
states and receipt validity. Do not give enablement commands until the plugin is installed and
the host has loaded its hooks. Existing enabled flags and a current receipt normally survive a
reinstall; report that state without pretending this is first-time enablement.

Before giving restart instructions, identify the current developer host and whether Codex is
running in an interactive terminal or the GUI. Honor the operator's explicit identification.
Otherwise inspect available host metadata, selected environment indicators (`TERM_PROGRAM`,
`TERM`, `SSH_TTY`) and process ancestry (process names only). Terminal indicators and a `codex`
ancestor can support terminal identification, but inherited environment variables or a sandboxed
process tree alone may be inconclusive. A tool subprocess without a TTY does not establish that
the developer host is the GUI. Do not dump the full environment or process arguments. If the
available evidence is ambiguous, ask which surface the operator is using before choosing restart
instructions. Do not assume the GUI from earlier test coverage or a previous session.

Give only the instructions for the identified host:

- Claude Code: restart Claude Code in `<TARGET_REPO>` so the installed hooks are loaded.
- Codex in a terminal: first tell the operator to start Codex in the root of the repository they
  want to work in. If the current directory is already that repository, exit Codex and restart it
  there with `codex`. Otherwise exit Codex, change to the intended repository root in the terminal,
  and run `codex` there. This fresh session loads the installed reviewer plugin and its MCP server.
  Then run `/hooks`, inspect the `reviewer-agent-codex` entries, and trust any requiring approval.
  After that, tell the agent to continue so it can verify the loaded components and proceed to
  settings confirmation. Do not put `/hooks` before the initial restart or require another restart
  afterward unless the host requests it or verification identifies a reload problem. Do not tell
  a terminal user to launch or restart the GUI. If the target changed, record and verify its new
  canonical root and inspect its own flags and receipt; do not carry state from the old target.
- Codex GUI: open an interactive CLI session by running `codex` in a terminal in `<TARGET_REPO>`,
  run `/hooks`, inspect the `reviewer-agent-codex` entries, and trust any requiring approval.
  `/hooks` is not a GUI command. Exit the CLI, fully quit and restart the GUI, then start a new
  chat in `<TARGET_REPO>` and tell the agent to continue.

If `/hooks` already shows the installed definitions trusted/enabled, record the operator's
inspection as completed; do not ask them to enable those same hooks again. Keep that observation
separate from the repository's commit-review and Stop flags. Request another trust action or
restart only when the host requests it or a specific failed check establishes the need.

After the operator returns, verify the canonical target, available reviewer tools, installed hook
mappings, feature flags and receipt. Use existing lifecycle evidence where available, stating
which event it proves: a recorded prompt proves prompt-hook execution, not completion review.
Do not invoke lifecycle tools manually. If these setup checks pass, report that briefly and
immediately present step 5's first unanswered settings screen. Do not wait for an extra “continue.”
If a check fails, name the failed check and the specific repair or operator action needed; keep
only dependent work pending. Completion/commit outcome verification remains separately tracked.

Describe the fresh session as loading the components, not as proof that either feature is active.
Hook trust and the repository's feature flags and risk receipt must still be checked. Codex's
[hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks) explains
that installed plugin hooks are skipped until their current definitions are trusted.

For Codex, explain that CLI smoke coverage is limited to stop supervision and does not cover
every terminal UI or the commit gate.

<a id="2-resolve-choices-once"></a>

### 5. Resolve choices and enable each feature in order

Begin this walkthrough only after installation, hook trust and host restart are verified.
Introduce it with: “We'll confirm each feature's settings, then choose whether to enable that
feature before moving to the next one.” Name the target repository. Explain that a current
receipt covers both features in that repository; it is not installation-wide.
Do not describe shared model, effort or timeout settings as belonging only to the target.

**Use an interactive conversational walkthrough, one screen at a time.** Explain the feature
in the conversation before asking for a choice; do not replace that explanation with a compact
question card or a bare list of values. Use the operator's requested interaction format. When
they ask for a conversational walkthrough or reject question cards, ask directly in chat rather
than using `request_user_input_async`, `request_user_input` or `AskUserQuestion`.

Include both features in the introductory explanation, following the update walkthrough's
independent-switch rule and its linked settings reference. Commit review checks staged changes
before a commit and returns findings for the developer agent to fix before retrying. Stop
supervision checks whether the authorized task is complete when the developer agent tries to
finish its turn. It can direct that agent to continue unfinished work, adding waiting and model
usage. Either feature, both, or neither may be enabled; changing one does not change the other.
Explain this behavior before the first settings question, then discuss each feature's settings
and enablement in the established order. Do not postpone the explanation of what Stop does until
after asking the operator to confirm settings shared with it.

At step 1, disclose that native Codex model and effort are shared with Stop across repositories.
At step 3, explain Stop-specific behavior: its fixed five-minute review budget is separate from the configurable
commit timeout; there is no reading delay. Escape interrupts a running review; typing a reply
is not a substitute. Label these timing and interruption behaviors fixed, not adjustable choices.
Use the host-specific Stop controls in the settings reference for Claude instead of applying
Codex's fixed behavior to it.

For each settings screen, explain what the system does, who acts on its findings, and how the
feature relates to the other feature. Then explain each setting's purpose, effective value,
source, practical trade-off and shared scope. Label adjustable settings and fixed behavior as
specified below. End with one clear keep/change question and wait for the answer. Do not combine
settings confirmation with enablement. The next screen handles that feature's on/off choice;
a conversational answer never substitutes for an exact trusted control command or risk acceptance.

**Required table format for every settings screen, in both install and update walkthroughs:**
After the feature explanation, show a Markdown table with columns **Setting**, **Current value
(source)**, **What it does / trade-off**, and **Adjustability / scope**. Include the group's actual
effective values, not assumed defaults. Use this format for commit review, Stop supervision,
and session rotation/history. Label rows **Adjustable**, **Adjustable but shared**, or **Fixed
behavior**, naming the affected features/repositories. Put adjustable rows first; show fixed
behavior separately from the choices, in a second table when needed. Do not put feature on/off
choices in the settings table; those belong to the next enablement screen. The table supplements
the explanation of system behavior rather than replacing it. End with one conversational
keep/change question and wait. Use a different format only if the operator explicitly requests it.

**Keep every screen short: roughly 15–25 lines.** The rules above say what to cover, not how much
to write. Use two or three plain sentences for what the feature does, then the table, then the
question. Keep each table cell to a few words; state a trade-off as a phrase, not a paragraph.
Group rarely changed settings in one row (for example, the codex backend's first-output and idle
timeouts). Say “no environment overrides” in one line instead of listing what you checked. The
introduction before step 1 (target, current on/off state, receipt, the five step names) takes at
most about eight lines. Operators reject long screens; when they do, shorten, do not re-explain.

Once the settings screen is presented, yield for the operator's answer. Finish independent guide
edits and checks before presenting that screen rather than continuing them behind a pending
question. If the operator explicitly asks for concurrent work, describe what will run. Silence,
a preselected option, tool acknowledgement or elapsed time is not an answer.

Use two separate screens for settings confirmation and feature enablement. Screen 1 presents the
current configuration and asks whether to keep or change it; it contains no disclaimer, enablement
command or on/off question. Wait for the user's answer. The very next screen after they confirm
or finish changing those settings must be screen 2 for that SAME feature: its enablement choice,
the short risk notice when acceptance is required, and the exact applicable operator command.
Use the enablement-screen reference below. Do not move to Stop settings after the user confirms
commit-review settings, and do not collect all settings groups before showing enablement.

**Required five-step walkthrough — show this order at the start:**

1. **Defaults for reviewer-agent:** explain commit review, show its effective settings/defaults
   in the required table, ask keep/change, and wait.
2. **Turn reviewer-agent on if you want it on:** present only commit-review enablement. If it is
   already on with a current receipt, ask whether to keep it on or disable it; keeping it on
   requires no repeated command. Otherwise use the applicable trusted command/skip path. Wait
   for the operator's choice and verify actual state before advancing.
3. **Defaults for stop-continue:** explain Stop supervision, show its effective settings/defaults
   and fixed behavior in the required tables, ask keep/change, and wait. Preserve shared values
   confirmed in step 1 unless the operator requests a change.
4. **Turn stop-continue on if you want it on:** present only Stop enablement, independently of
   commit review. Use the same retained-state or trusted command/skip handling, wait for the
   operator's choice, and verify actual state before advancing.
5. **Other defaults:** explain and show session rotation, history retention and any applicable
   host-specific remaining settings; ask keep/change and wait. Label fixed values accurately.

Label each conversational screen “Step N of 5” using these names. These are five separate
operator exchanges, not five sections in one response. A settings answer never confirms an
on/off choice. “Defaults” means show the effective value and its source, including saved choices;
it does not mean reset to factory values. Start over at step 1 when the operator requests a fresh
walkthrough, preserving installed state and reporting it accurately.

Keep each screen within its step. The introduction may briefly explain both features and their
independent switches, and step 1 must disclose that model/effort are shared with Stop. Defer
Stop-specific settings, timing, interruption details and tables to step 3; defer other-defaults
tables to step 5. Never merge steps 1 and 3, skip steps 2 or 4, or ask several steps' questions at
once. Once a screen's question is asked, yield and wait before proceeding.


If installation or host reload is incomplete, finish those prerequisites first instead of starting
screen 1 and later skipping its enablement screen. Never offer commands for an uninstalled plugin.
An answer such as “keep” on screen 1 confirms settings only, not risk acceptance or enablement.
For a reinstall, distinguish retained on/off flags from an active feature: the plugin is absent
until reinstalled, and those saved flags normally take effect again after installation and reload.

Use the current developer host unless the prompt explicitly names another one. Installing the
package and trusting its hooks do not enable commit review or stop supervision. Each feature is
selected separately and requires repository enablement after operator risk acceptance. State
up front that commit review and Stop supervision can each be turned on or off independently:
the user may enable commit review only, Stop supervision only, both, or neither. Shared model
settings and a shared risk receipt do not couple the feature switches.

Inspect the installed host/backend and resolve the effective settings, including environment and
saved command overrides. Walk a new user through the three groups below, one group at a time,
using the two separate exchanges for each feature described above.
For each setting, explain its purpose, the practical trade-off and the effective value before
asking whether to keep or change it. Label each item explicitly: **Adjustable**, **Adjustable but
shared** (name the other affected features/repositories), or **Fixed behavior** (no supported
setting). Put adjustable choices first and fixed behavior separately; ask for decisions only on
adjustable items. Show only the installed host's relevant controls. Do not present the reference
table below as an unexplained questionnaire or ask about all groups at once.

Keep configuration-source details secondary: “default” means the plugin's built-in value; a saved
override is an earlier explicit choice; an environment override takes priority over saved settings.
Honor choices made at the proper checkpoint unless the operator requests a fresh walkthrough.
This does not make a premature pre-install answer valid; follow the recovery rule above. An
experienced user may choose to confirm all displayed settings together after the prerequisites
have passed. Once a settings question is presented, wait for the answer unless the operator
explicitly requests concurrent work. Silence does not authorize changes. Do not silently choose
a Claude backend or install unused backends.

#### Group 1 — commit review

Explain that this feature checks staged changes before a commit. On rejection, the reviewer
returns a list of problems for the developer agent to fix before retrying review. Do not tell the
user they must fix the findings. Explain here that commit review can be enabled or disabled
independently of Stop supervision: either one, both, or neither may be active. Turning commit
review off does not turn Stop supervision off. Discuss the relevant settings on screen 1:

- **Backend and model:** the backend runs the independent review; the model is the AI that judges
  the changes. This does not change the developer agent's model. Describe available choices and
  account availability without promising that one model will catch every defect. Model choice
  can affect review quality, latency and usage cost. For Claude, first resolve the backend choice.
- **Reasoning effort:** how much reasoning the reviewer is asked to spend. Higher effort can take
  longer and use more resources; it does not guarantee a correct review. Show the effective level
  and only levels supported by the selected backend/model.
- **Commit timeout:** the maximum audit budget, not a mandatory wait. An approved review can
  finish sooner. Too short a budget can interrupt legitimate work; a longer budget permits a
  longer wait before failure. Display seconds and a familiar duration, such as “3600 seconds
  (1 hour).” If applicable, explain first-output and idle timeouts as separate limits on silence,
  and allow for long-running tests before reducing them.

End screen 1 with one question naming the actual values: “Keep model [model], effort [level]
and timeout [duration], or change any of them?” Explain global scope before changes. A
conversational “yes” confirms these settings only. Immediately after confirmation, present the
commit-review enablement screen from the reference below. Resolve and verify that choice before
starting the Stop-supervision settings group.

#### Group 2 — Stop supervision

Explain that this feature checks whether the agent has completed the authorized task when it
tries to finish its turn. It can direct the agent to continue, so it can cause additional work,
waiting and model usage. Repeat here that the two switches are independent: the user may enable
Stop supervision only, commit review only, both, or neither. Turning Stop supervision off does not
turn commit review off. On screen 1, discuss the model and effort used by Stop; shared choices
already confirmed in this walkthrough stay in effect unless the user requests a change. Do not
repeat commit-only timeout choices as Stop settings. Reserve the on/off choice for screen 2.

Explain which model/effort settings it shares with commit review and which, if any, are separate.
For native Codex, the same model and effort apply, there is no reading delay, and the fixed
five-minute review budget is separate from the configurable commit timeout. Describe fixed
behavior without offering an unsupported setting. Explain that Escape interrupts a native review;
typing a reply is not a supported substitute. For Claude, explain its configurable reading delay
as time to read/respond before review starts, its separate supervisor effort, and the optional
watchdog as a periodic recovery check that can prompt unfinished work to resume. Do not enable
that watchdog merely because Stop supervision was selected.

For native Codex, present the distinction explicitly:

| Item | Can the user change it? |
|---|---|
| Stop supervision on/off | **Adjustable**, per repository and independent of commit review. |
| Reviewer model and reasoning effort | **Adjustable but shared** with commit review across repositories; no Stop-only selection. If already confirmed in group 1, keep that answer unless the user requests a change. |
| Reading delay | **Fixed behavior:** none; no supported delay setting. |
| Five-minute review budget | **Fixed behavior:** no supported Stop-timeout setting; the commit timeout does not change it. |
| Escape interruption | **Fixed behavior**, not a setup preference. |

End screen 1 by asking whether to keep the displayed shared model/effort or change either.
For Claude, also ask about its supported timing/effort choices. Immediately after confirmation,
present Stop's enablement screen, separately from fixed behavior. If Stop is the first feature
enabled and no current
receipt exists, use the same short risk notice and operator-command step as for commit review.
Choosing Stop must not enable commit review.

#### Group 3 — reviewer sessions and retained history

- **Session rotation:** after the configured number of completed non-retry commit audits, retire
  the reviewer's conversation and start a fresh one for the next audit. This bounds accumulated
  context but requires fresh briefing. It does not delete saved review history. Explain that
  rejected attempts and their retries belong to one audit; zero disables automatic rotation.
- **History retention:** how many verdict/outcome entries are kept per repository. More entries
  provide a longer diagnostic record and retain more reviewed-code content on disk; fewer give
  less history to inspect. This is an entry count, not a number of days or sessions. Lowering
  the limit can prune older records when the next entry is written. Rotation and retention are
  different controls even though both involve previous reviews.

End with one question: “Keep rotation after [count] completed audits and [count] history entries
per repository, or change either?” Summarize the confirmed choices before applying changes and
keep any unanswered group pending. Configuration confirmation does not accept the disclaimer or
enable a feature; feature enablement is resolved on its own screen above.

The following table is the installing agent's host-specific reference, not the user walkthrough:

| Setting to discuss | Native Codex | Claude Code plugin |
|---|---|---|
| Reviewer backend and model | Native Codex child reviews; built-in model `gpt-5.6-luna`. Show the effective selection rather than assuming this default. | Ask which available backend: `codex`, `pi` or `claude`. Show its effective model. Unset, codex uses `gpt-6-luna`; pi and claude use their CLI's default. |
| Reasoning effort | Built-in `xhigh`, shared by commit and Stop reviews. | Commit effort is backend-specific (codex default `xhigh`); Stop has separate `stopContinueEffort`, default `low`. |
| Commit timeout | `commitReviewTimeoutSeconds`, default 3600 seconds; integer 60–7200. Configure through JSON/environment, not a model-command timeout argument. | `commitReviewTimeoutSecs`, default 1800 seconds; use `/reviewer-agent:reviewerModel timeout <seconds>` and its host-ceiling validation. |
| Streaming timeouts | No separate user-configurable first-output/idle settings. | Codex backend only: first output 30 seconds, idle 900 seconds by default. Ask whether long silent tests need a larger idle allowance. |
| Stop timing | No reading delay; fixed 300-second review budget inside a 360-second hook ceiling. These are not commit-timeout settings. | Offer the reading delay, default 60 seconds, range 0–300, and separate supervisor effort. |
| Session/history preferences | Offer current rotation and retention; defaults are 3 completed commit audits and 100 retained entries per project. | Offer current rotation and retention; defaults are 3 completed commit audits and 100 retained entries per project. Ask about the optional watchdog only if Stop is selected. |

Use the [native settings reference](plugins/reviewer-agent-codex/README.md#reviewer-model) or
[Claude settings reference](README.md#claude-code-configuration) for supported controls, scope,
precedence and backend-specific effort levels. Do not invent independent commit/Stop models or
native Stop delay controls. A syntactically valid model/effort is not proof that the operator's
account supports it; report reachability failures during verification.

Explain scope before writing: native model/effort and timeout configuration affects other
repositories too; Claude model/effort slots are global per backend and its commit timeout is
shared. Preserve unrelated keys. Show the effective values and sources again after changes and
after host reload; a higher-priority override can mask a successful configuration-file edit.
If a supported control requires a trusted user invocation, provide that exact command at the
operator checkpoint rather than bypassing its hook.

<a id="5-human-only-checkpoint-and-final-report"></a>

### 6. Enablement-screen reference and final report

This is the screen-2 reference used immediately after each feature's settings confirmation in
step 5, not a separate phase to postpone until all settings groups are finished.

**Codex command entry through autocomplete:** On the applicable enablement screen, explain
this shortcut before the final command/skip instructions. For reviewer-agent, type `$reviewer`,
select **Reviewer Agent** from the suggestions, and press Enter to insert the skill. For
stop-continue, type `$stop`, select **Stop Continue**, and press Enter to insert it. Choose the
entry belonging to `reviewer-agent-codex` if several plugins offer similar names. Then append a
space and `on acceptRisk` if the repository's receipt is missing/stale, or `on` if it is current,
and submit the completed command. Selecting a suggestion only inserts the skill; it does not
submit the enablement command or accept risk. Show only the current feature's shortcut on its
screen. Keep the complete exact namespaced command below as a copy/paste fallback when
suggestions are unavailable. Never submit `$reviewer on` or `$stop on` as abbreviated commands.

Autocomplete does not change the operator-only acceptance requirement or the standalone-command
format. Keep the short risk notice when acceptance is required. After the ON response, the
operator checks the target repository and sends `continue` separately to resume setup.

Use screen 2 for one feature at a time once the host prerequisites above are complete. Show
its actual state and receipt validity first. Keep explanations, the target repository and any
required short disclaimer notice before the final action instructions. For a currently disabled
native Codex feature, end the message in this form, substituting its exact applicable command:

> If you want to enable this feature, type:
>
> `<exact command>`
>
> After the `ON` message, check that it names the intended repository, then type **continue** as
> a separate message.
>
> If you don't want to enable it, just type **continue**.

Do not append another question, skill explanation or other lines after those instructions.
Use `on acceptRisk` only for a missing/stale receipt; otherwise use plain `on`. After `continue`,
read the actual state: if enabled, verify its receipt; if it is still off, the skip instruction
makes that an explicit choice to leave it off. This exception applies only to a screen that
explicitly offered that skip path. A generic “keep”, “yes” or “continue” elsewhere must not be
interpreted as an enablement choice. Do not call a still-pending command successful.
For an already enabled feature with a current receipt, ask whether to keep it enabled or change
it; do not request new acceptance or pretend that `continue` would turn it off. Keep each feature
independent and follow its trusted operator controls for requested changes.

When the user says they want a feature enabled and no current repository receipt exists:

1. Say explicitly that their answer selected the feature/settings but did **not** accept the risk.
2. Show the short acceptance notice below, with the disclaimer link and the exact command for
   the selected host/feature. Do not paste the full disclaimer into the conversation. Displaying
   the command is an instruction to the user, not an invocation or evidence of acceptance.
3. Once hook trust/restart and target prerequisites are complete, ask the user to type the exact
   applicable `on acceptRisk` command **as their own standalone message in the host session opened
   in `<TARGET_REPO>`**. A shell `cd` does not change that session's repository. Explain that this
   command records acceptance and enables the named feature. “Yes,” “looks good,” trusting hooks,
   or an agent repeating the command does not satisfy this step. Never submit it for the user,
   invoke a toggle on their behalf, or write an acceptance receipt yourself.
4. For Codex, tell the user: “After the `ON` message, type `continue` as a separate message so I
   can verify enablement and resume setup.” The exact control hook intentionally ends its turn
   without invoking the agent. Do not append `continue` to the acceptance command, because the
   hook requires an exact standalone invocation. Ask the user to check that the `ON` message
   names `<TARGET_REPO>` before continuing; if it names another repository, report the mismatch.
5. After the user types `continue`, check the reported canonical repository, the receipt against the installed disclaimer, and the named feature's
   enabled state. Confirm the other feature still matches the user's choice. An echoed command
   or a missing refusal is not evidence of successful enablement. If the Codex exact invocation
   reaches the model instead of its trusted hook, follow the bundled skill: ask the operator to
   start a new plugin-enabled thread and repeat it; do not substitute a script.

For native Codex commit review, show the user:

> This software runs an autonomous AI agent inside your development environment, with access to
> your source code. It can execute commands in your repository or direct your development agent
> to do so. Using it is entirely at your own risk.
>
> To use it, you accept the risk and acknowledge that you have read the
> [disclaimer](https://github.com/rladr-org/reviewer-agent/blob/main/DISCLAIMER.md) and agree to its terms.
>
> If you agree and want to enable the software, type:

```text
$reviewer-agent-codex:reviewer-agent on acceptRisk
```

After the `ON` message, type `continue` as a separate message so the agent can verify enablement
and resume setup. Check that the message names the intended repository first.

The user must type the acceptance command at the prompt as their own standalone message. Show only
the applicable command; for Stop-first or Claude setup, substitute the selected feature's command
from the table below. Ensure the prompt belongs to `<TARGET_REPO>` before inviting acceptance.
The installed package's disclaimer remains the notice checked by the local receipt.

For first-install verification, the user may first type the applicable plain `on` command; without
a current receipt it must refuse and display the accepting form. That refusal is not acceptance.
Show only the applicable host/feature pair from this reference:

| Host / first selected feature | Plain invocation | Accepting invocation |
|---|---|---|
| Claude / commit review | `/reviewerAgent on` | `/reviewerAgent on acceptRisk` |
| Claude / Stop supervision | `/stopContinue on` | `/stopContinue on acceptRisk` |
| Codex / commit review | `$reviewer-agent-codex:reviewer-agent on` | `$reviewer-agent-codex:reviewer-agent on acceptRisk` |
| Codex / Stop supervision | `$reviewer-agent-codex:stop-continue on` | `$reviewer-agent-codex:stop-continue on acceptRisk` |

One current repository receipt covers both features, but acceptance enables only the feature
named in the invocation. If both were selected, the human can then enable the second with its
plain `on` command. After each exact Codex control command, the user types `continue` separately
to resume the agent's setup walkthrough. If only one was selected, leave the other off; do not require enabling commit
review to use Stop supervision. If neither was selected, skip enablement and risk acceptance.
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

When installation/update and the required setup checks are complete, lead the final response
with “Installation is complete and set up for <TARGET_REPO>” (or “Update is complete” for an
update). Do not describe hypothetical changes or distinguish walkthrough choices from
configuration in vague terms.
Report a concrete unresolved mismatch if there is one, and do not claim completion while a
required setup step is pending. Keep detailed verification evidence in the handoff; summarize
material limits accurately without obscuring the completion result.

For native Codex, finish by explaining that the plugin does not need reinstalling for each
repository: start `codex` in the other repository and enable either or both features there.
Show these commands again, labeled by feature:

```text
$reviewer-agent-codex:reviewer-agent on
$reviewer-agent-codex:stop-continue on
```

Submit each selected command separately. If the repository has no current risk receipt, the
first plain `on` refuses and displays the accepting form: read the disclaimer, then, only if
agreeing, repeat that feature's command with `on acceptRisk`. One current receipt covers both
features in that repository; it does not enable the other feature. A new repository also needs
its own project-specific `reviewerAgent.md` for commit review; preserve an existing customized
brief and resolve a missing brief before enabling that feature. Existing shared settings remain
in effect. Autocomplete via `$reviewer` or `$stop` is available as described above.

Report the host, selected backend where applicable, requested features, canonical target root,
installed version, effective model/effort/timeouts and their sources, the operator's settings
choices, test results, every external path changed, and any warning. When commit review was
selected, state that the first real commit after operator acceptance is its end-to-end verification.
For a Stop-only installation, commit-gate verification is not applicable. Report
enablement, commit-gate verification, and Stop verification separately, marking any unperformed
check as pending. Do not infer that an existing task has reloaded from the installer succeeding;
verify the loaded components after restarting the host, or use a fresh session.
