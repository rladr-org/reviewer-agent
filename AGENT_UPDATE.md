# Updating reviewer-agent unattended


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

A procedure for an **agent** told to update an existing installation of reviewer-agent
(`reviewerAgent`, `stopContinue`, `stopContinueWatchdog`, and the `AskUserQuestion` reroute) to
the most recent version. Every step has a command and a check that proves it worked. Do not
proceed past a failed check.

This guide updates the **Claude Code** plugin. For the Codex-native plugin, see
[the Codex update section](#the-codex-native-host) below and
[`plugins/reviewer-agent-codex/README.md`](plugins/reviewer-agent-codex/README.md#update-disable-and-uninstall).
If nothing is installed yet, this is the wrong document: use [`AGENT_INSTALL.md`](AGENT_INSTALL.md).

If you are a human, the [README's *Update* section](README.md#update-disable-and-uninstall-claude-code)
is shorter.

---

## Resume after operator actions

Follow the [installation checkpoint rule](INSTALL_WITH_AGENT.md#continue-across-operator-checkpoints)
when yielding for hook trust, restart, or a trusted control command. When the operator reports
completion in ordinary language, verify that checkpoint and resume the first unfinished update
step in the same turn. Do not end with a status-only acknowledgement or require an extra
`continue`. Already-trusted hooks need no repeated enable action. Preserve settings choices
already confirmed in this update; ask only the next unanswered question. A restart/trust report
alone does not confirm settings or change repository feature choices.

## Confirm existing settings with the user — both hosts

Introduce the walkthrough as a review of installation-wide settings followed by the selected
repository's feature enablement and risk acceptance. Name that repository and preserve any
current acceptance receipt; shared settings and repository-specific state have different scopes.

**When:** run this walkthrough **after** the update and restart (step 5), in the restarted session
opened in the target. Then each enablement screen's command can be typed at once, and it records
the new release's disclaimer. Before the update, only record a **baseline**, without asking
anything: identify the target repository and note its current feature states, receipt validity,
selected backend, effective reviewer model, reasoning effort, and timeouts. Include applicable Stop delay/effort, session rotation, history retention and
Claude watchdog settings. Show where each effective value comes from: environment, saved command
override, JSON or default. Inspect only plugin settings; do not print unrelated configuration.
Use the host-specific controls and defaults in
[the installation settings checklist](INSTALL_WITH_AGENT.md#2-resolve-choices-once).

**Claude Code: how to read the effective values and their sources.** The plugin's show forms are
read-only and print each value with its source. From a session opened in the target they are
`/reviewer-agent:reviewerModel`, `/reviewer-agent:reviewerSession`, `/reviewer-agent:reviewerHistory`,
`/reviewer-agent:stopContinue delay status` and `/reviewer-agent:stopContinueWatchdog status`.
The agent doing the update is usually not in the target, so it runs the same scripts directly,
from the target repository (`<PLUGIN_DIR>` is the checkout or installed plugin directory):

```bash
cd <TARGET_REPO> && python3 <PLUGIN_DIR>/reviewer_cmd.py model ""
cd <TARGET_REPO> && python3 <PLUGIN_DIR>/reviewer_cmd.py session ""
cd <TARGET_REPO> && python3 <PLUGIN_DIR>/reviewer_cmd.py history ""
cd <TARGET_REPO> && python3 <PLUGIN_DIR>/reviewer_cmd.py stopcontinue "delay status"
cd <TARGET_REPO> && python3 <PLUGIN_DIR>/reviewer_cmd.py watchdog status
```

For the baseline, `<PLUGIN_DIR>` is the old installed code; for the walkthrough, the updated code.
A version older than 1.4.3 has no `delay` form; its `stopcontinue` prints a `usage:` line instead,
changes nothing, and that version has no configurable Stop delay. On each screen, name every
value the update changed against the baseline, such as a new default. Do not run the `agent` or `stopcontinue` subcommands with any
other argument: those change state.
These show nothing for the supervisor's effort. Resolve it by hand: `REVIEWER_STOPCONTINUE_EFFORT`
in the hooks' environment, else `stopContinueEffort` in `~/.claude/reviewer-agent/config.json`,
else `low`. Feature state is the `<project-stem>.active` (commit review) and
`<project-stem>.stopcontinue.active` (Stop) files in `~/.claude/reviewer-agent/state/`. The receipt
is `<project-stem>.risk.accepted.json`: it is current only while its `disclaimer_sha256` equals
`sha256sum` of the **installed** `DISCLAIMER.md`. If the update changed `DISCLAIMER.md`, the
receipt is stale after the update; see the screen-2 rule for a revised disclaimer.

Explain in both feature groups that commit review and Stop supervision have independent on/off
switches: the user may enable either one, both, or neither. An update must preserve the confirmed
combination, and changing one switch must not silently change the other. Shared settings and a
shared receipt do not require enabling both features.

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

**Screen 1 — confirm the current settings/defaults.** Show the effective configuration values,
their sources, what each setting does and its trade-offs. A saved override is a current setting,
not a built-in default. Explain the feature's behavior, and report its current on/off state as
context, not as a confirmed preference. For commit review, explain that the reviewer checks staged
changes before a commit. On rejection, the reviewer returns a list of problems for the developer
agent to fix before retrying review; do not tell the user that they must fix those problems.

End with a question naming the configuration values: “Keep these settings, or change any of them?”
Do not include the disclaimer, enablement command or on/off question on this screen. Wait for the
user's answer before applying configuration changes or presenting screen 2. Reporting that values
match a handoff or earlier choice does not replace this confirmation in a fresh walkthrough.
An answer such as “keep those” confirms the displayed configuration values only, not enablement
or risk acceptance. Do not treat a request to continue the update as configuration confirmation.

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

**Screen 2 — choose enablement and present risk acceptance.** After screen 1 is confirmed, present
the conditional enablement action and skip path below. For a missing or stale receipt,
show the short risk notice, disclaimer link and exact applicable `on acceptRisk` command from the
[operator checkpoint](INSTALL_WITH_AGENT.md#5-human-only-checkpoint-and-final-report).
Do not paste the full disclaimer. Explicitly prompt: “If you want this feature enabled, send this
exact command as your own standalone message.” Explain that keeping a disabled feature off
requires no command or acceptance. With a current receipt, show the applicable plain `on` command
if enablement is needed; do not request acceptance again. Preserve an already enabled feature
when the user chooses to keep it on. Show only the command for the host and feature being discussed,
and explain that it enables only that feature.

**Same ending on both hosts, for both features.** Every screen 2 (commit review at step 2,
Stop supervision at step 4) ends with the exact command to type, then `continue`, or `continue`
alone to leave the feature off. Claude Code uses the same form as Codex:

> If you want to enable it, type:
>
> `/reviewerAgent on acceptRisk`   (Stop: `/stopContinue on acceptRisk`)
>
> After the ON message, check that it names `<TARGET_REPO>`, then type **continue** as a
> separate message.
>
> If you don't want to enable it, just type **continue**.

Use `on acceptRisk` when the receipt is missing or stale, plain `on` when it is current. Nothing
follows those lines.

**A revised disclaimer requires accepting again, even for an enabled feature.** When the update
changes `DISCLAIMER.md`, the existing receipt no longer matches, so treat each enabled feature as
needing acceptance: show the short risk notice, the disclaimer link and the `on acceptRisk`
ending above. Do not offer “keep it on as it is.” The accepting command hashes the **installed**
disclaimer, so the operator types it **after** the update and restart, in a session opened in
the target, which is where this walkthrough runs. If they type only `continue`, the feature must end up off: the hooks of both hosts
check only the feature flag, not the receipt, so a feature left on would keep running under the
stale receipt. Turn it off with the feature's `off` command from the target and verify the flag.

For Codex, screen 2 must also say: “After the `ON` message, check that it names the intended
repository, then type `continue` as a separate message so I can verify enablement and resume.”
Name the actual target repository. Do not append `continue` to the `on acceptRisk` or plain `on`
command: the trusted control hook requires an exact standalone invocation and ends its turn
without resuming the agent. After the separate `continue`, verify the canonical repository,
receipt validity against the installed disclaimer, and actual feature state before reporting
successful enablement or advancing the walkthrough. Displaying a command is not execution or
acceptance, and a conversational choice to enable does not run it for the user.

For a currently disabled native Codex feature, put the risk notice and explanations before the
final action instructions. End with: “If you want to enable it, type:” and the exact command,
then “After the ON message, check the repository and type continue as a separate message.”
The last line must be: “If you don't want to enable it, just type continue.” Do not append another
question, skill explanation or any lines after that. When the user returns with `continue`, check
actual state: an enabled feature must have a current receipt; if it is still off, the explicit
skip path means leave it off. This does not apply to an already enabled feature: ask whether to
keep it enabled or change it, without requesting new acceptance when its receipt is current.

**Do not infer an enablement preference from an unspecified or ambiguous answer.** If the user
does not clearly choose on or off on screen 2 and has not used the explicit `continue` skip path
above, ask directly: “Do you want this feature enabled or disabled?” Leave its current state
unchanged, mark the choice pending, and do not describe that
unchanged state as a confirmed preference. If they choose enablement without submitting the
required command, prompt them to submit it themselves. Do not move past a pending enablement
choice or treat silence as either consent or refusal.

Do not assume familiarity with terms such as effort, timeout, rotation or retention. Label every
item **Adjustable**, **Adjustable but shared** (with its scope), or **Fixed behavior**. Present fixed
behavior separately and ask for decisions only on supported adjustable controls. Once the user
has answered a group's keep/change question in this walkthrough, retain that answer unless they
request another change. Finish independent source/test checks before asking, then wait for the
answer unless the operator explicitly requests concurrent work. Do not silently reset settings,
adopt new defaults or apply unanswered preference changes.

Record the answer. Explain any global setting's effect on other repositories before
applying a requested change. After applying changes, resolve the settings again and
compare them with what the user confirmed, including changes caused by defaults or higher-priority
overrides. Report mismatches and resolve them before declaring configuration verification complete.

Hook trust, risk acceptance and feature enablement are separate. “Trust all hooks” only permits
hooks to execute. An update normally retains current receipts and feature flags; verify that for
the actual target. Present enablement instructions only on screen 2 of each feature group as
specified above, using the short risk notice rather than the full disclaimer. If the user chooses
to enable a feature with a missing or stale receipt, require their own exact `on acceptRisk`
invocation through the
[operator checkpoint](INSTALL_WITH_AGENT.md#5-human-only-checkpoint-and-final-report).
If the user says to keep a disabled feature off, record that choice and proceed to the next group;
no command or acceptance is required for that feature. If their answer does not clearly specify
the feature's on/off choice, follow the clarification step above instead of assuming either choice.
A conversational “yes” confirms settings/intent only. Only the user may submit the accepting
invocation. For Codex, explicitly tell the user to check the repository in the `ON` message,
then type `continue` as a separate message; the control hook ends its turn without resuming the
agent. Verify the canonical target, current receipt and actual feature state afterward. Do not enable a previously disabled feature
unless requested, and do not ask for new acceptance when the existing receipt is current.

## The one thing that trips up every local update

**`claude plugin update` decides whether there is anything to do by comparing the `version` string
in the manifest, not the file contents and not the Git commit. This holds for both marketplace
sources:**

- A **GitHub marketplace** source (the normal end-user case). `claude plugin marketplace update`
  fetches the latest commit. But if that commit carries the same `version` as the installed copy,
  `claude plugin update` prints `already at the latest version` and copies nothing. Verified with
  Claude Code 2.1.302 on 2026-10-17: main had moved from `1de0d8f` to `fffa4f5` under the same
  1.4.2 label, and the update was a no-op. End users are unaffected, because every release bumps
  the version. Commits pushed between releases are what it misses.
- A **local Directory** source — a checkout you `marketplace add`ed by path. That is the
  developer case, and also every installation made with [`AGENT_INSTALL.md`](AGENT_INSTALL.md).
  **If the version string has not changed, `claude plugin update` copies nothing, even
  though the files on disk have changed.** A day of committed work behind an unchanged `version`
  is invisible to it. Nothing fetches new commits into the checkout either: an end user's
  checkout gets the new release only from `git pull` (step 2).

**A Directory source's hooks run from the checkout itself, not from the cached copy.** Verified
with Claude Code 2.1.303 on 2026-10-19: the live `PreToolUse` hook ran
`python3 <checkout>/commit_hook.py`, while `installed_plugins.json` named the `…/1.3.1/` cache
directory. So for a Directory source, changing the checkout's files changes the code every
running session executes, at once, under that session's existing hook list. `git pull` is the
moment the new code goes live, and `claude plugin update` mostly updates the recorded metadata.

So **a release must bump the version**, or installed copies stay stale with no error (see
[Release procedure](CONTRIBUTING.md#release-procedure) and the checklist at the end of this file).
A forced reinstall at the same version is the fallback. Determine which source you have at step 1.
To test commits **before** they are released, do not bump; follow
[*Testing an unreleased build without a version bump*](#testing-an-unreleased-build-without-a-version-bump).

One more Directory-source subtlety worth knowing before you rely on the recorded metadata: an
install **copies the working tree** at update time (uncommitted edits included), but records the
current **`HEAD`** as `gitCommitSha`. So uncommitted local changes *are* installed, yet the
recorded sha will not reflect them. For a release you can trust, commit first, then update.

## Read this before running anything

An update writes outside the repository. The full list, before the first command:

| path | what an update does | reversible? |
|---|---|---|
| Directory source only: the checkout (`<PLUGIN_DIR>`) | `git pull --ff-only` (step 2) brings in the new release; running sessions execute these files directly | yes, `git -C <PLUGIN_DIR> reset --hard <previous HEAD>` (record it first) |
| Claude Code's plugin cache (`~/.claude/plugins/cache/reviewer-agent/…/<version>/`) | a **new** version directory is copied in; the previous one remains until the host garbage-collects it | yes, roll back by reinstalling the prior version |
| `~/.claude/plugins/installed_plugins.json` | the `reviewer-agent` entry's `version`, `installPath`, `gitCommitSha` and `lastUpdated` are rewritten | yes, reinstall |
| `~/.claude/reviewer-agent/` (flags, risk receipts, backend selection, sessions, verdicts, logs) | **preserve and verify** — compare feature flags and receipt validity after update; a current receipt needs no re-acceptance. Confirm effective settings with the user before changing them | n/a |
| `~/.pi/agent/extensions/git-read-only/` | **nothing** — a plugin update does **not** refresh the Pi extension. If the extension changed and you use the `pi` backend, refresh it by hand (step 6) | yes, delete the directory |
| the **running** Claude Code session | Directory source: the scripts change at the `git pull`, the hook list at the restart. GitHub source: after a version bump, **nothing takes effect until restart** — the hook list is snapshotted per session, so the live session keeps running the old code (the gate still works, on the old version) until Claude Code is restarted. A same-version reinstall is the exception: it replaces the scripts in place, so the live session runs the new scripts under its old hook list (step 5) | n/a |

**What this gates is a fail-closed commit gate.** An update never disables it. Throughout the
version-bump update the repository stays gated on the old code (for a Directory source, until the
pull in step 2), and after the restart it is gated on the new code. The one gap is the uninstall-then-reinstall fallback (step 4), which is briefly
ungated between the two commands — a reason to prefer the version bump; on the version-bump path, at
no point is it ungated. That is intended.

**Stop and report, do not improvise, if:** the checkout's own suite fails (you are about to ship a
gate whose tests fail); `claude plugin update` reports `already at the latest version` for a
Directory source whose files you changed and you cannot bump the version; a standalone predecessor
is still registered (see below); or any check below does not produce the stated result.

**Stop if a standalone predecessor is still registered.** The same caution as installation applies
to updates. Inspect the user hook commands:

```bash
python3 -c "import json,os;p=os.path.expanduser('~/.claude/settings.json');d=json.load(open(p)) if os.path.exists(p) else {};print('\n'.join(f'{event}: {h.get(\"command\", \"\")}' for event,groups in (d.get('hooks') or {}).items() for group in groups for h in (group.get('hooks') or []) if 'revieweragent' in h.get('command','').lower() or h.get('command','').endswith('/bash_gate.py')))"
```

If it prints commands under `~/.claude/reviewerAgent/` or `~/.claude/hooks/bash_gate.py`, the
pre-plugin standalone is still active. Running both copies causes duplicate audits, and older
`SessionStart` hooks launch Codex even when Pi is selected. Follow the uninstall guide's
[backup](AGENT_UNINSTALL.md#step-0--back-up-first) and
[legacy-hook removal](AGENT_UNINSTALL.md#step-2s--tear-down-the-legacy-standalone-the-delicate-one)
steps before continuing. Preserve retained state and unrelated hooks; do not run optional data
purges for this migration. Read the `bash_gate.py` caution there; do not simply delete it.

---

## Step 1 — identify what is installed and its source

```bash
claude plugin list                 # note that reviewer-agent@reviewer-agent is enabled
claude plugin marketplace list     # note the reviewer-agent marketplace's Source
python3 -c "import json,os;e=json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['reviewer-agent@reviewer-agent'][0];print(e['version'], e['installPath'], e.get('gitCommitSha'), e['lastUpdated'])"
```

**Check:** you can state (a) the installed version, and (b) whether the reviewer-agent marketplace
`Source:` is `GitHub (…)` or a local directory. The rest of the procedure branches on that source.
`<PLUGIN_DIR>` below is that checkout path for a Directory source.

- **Installed version:** take it from the `installed_plugins.json` line (version, install path,
  commit, last update). For a local directory source, `claude plugin list` prints
  `Read from: <checkout>` and the version label currently in that checkout's manifest, which is not
  necessarily what was installed. Seen with Claude Code 2.1.303 on 2026-10-19: `plugin list` showed
  1.4.3 while the recorded installation was still 1.3.1 in the `…/1.3.1/` cache directory.
- **Source label:** Claude Code 2.1.303 prints a local source as `Folder (/path/to/checkout)`; older
  versions print `Directory (/path/to/checkout)`. Both mean the Directory source in this guide.

## Step 2 — fetch, verify, then pull the new release (Directory source)

Skip to step 3 for a GitHub source. A Directory source's hooks run from the checkout
([above](#the-one-thing-that-trips-up-every-local-update)), so the moment the checkout changes, the
new code is live. Prove the new release **before** it reaches the checkout — do not put a gate whose
own tests fail in front of the target.

**2a. Fetch and see what is new.** The checkout must be clean and on a branch that tracks the
release source (`origin/main` for a clone of the GitHub repository):

```bash
git -C <PLUGIN_DIR> status --short --branch      # no file lines; first line names the upstream
git -C <PLUGIN_DIR> rev-parse HEAD               # record: the rollback point
git -C <PLUGIN_DIR> fetch
git -C <PLUGIN_DIR> log --oneline -1 '@{upstream}'
git -C <PLUGIN_DIR> rev-list --count 'HEAD..@{upstream}'
```

**Check:** `status` lists no files, and the count is greater than 0. A count of 0 means there is
nothing to update: stop and report. If `status` lists files, stop and report — local edits are
the operator's, and a pull would mix them into the gate (and an install would copy them; see
above). Do not stash or discard them.

**2b. Test the fetched release in a temporary worktree**, outside the checkout. Running the suite
in a separate tree leaves the live checkout untouched until it passes. The suite needs pytest and
the other development dependencies, which the plugin itself does not use and which a normal
`python3` usually lacks (`No module named pytest`). Choose a new `<TEST_VENV>` and a new
`<TEST_TREE>`, both outside the checkout; both may be temporary. Use a Python 3.9+ interpreter
for `python3`:

```bash
git -C <PLUGIN_DIR> worktree add --detach <TEST_TREE> '@{upstream}'
python3 -m venv <TEST_VENV>
<TEST_VENV>/bin/python -m pip install -r <TEST_TREE>/requirements-dev.txt
cd <TEST_TREE> && <TEST_VENV>/bin/python -m pytest -m smoke -q -p no:cacheprovider -rs
cd <TEST_TREE> && python3 tools/check_split_layering.py
cd <TEST_TREE> && python3 tools/release_versions.py
```

`-m smoke` is the install-time subset and takes under a minute. A published release has already
passed the full suite (about 1,700 tests, 5–12 minutes); that is for maintainers
([`CONTRIBUTING.md`](CONTRIBUTING.md#running-the-suites)), not for an update.

**Check:** every smoke test passes, with only the skips listed in
[`AGENT_INSTALL.md` step 3](AGENT_INSTALL.md#step-3--verify-the-checkout-before-trusting-it);
`check_split_layering.py` prints `PASS [VERIFY]: acyclic, zero back-edges`; and
`release_versions.py` prints `PASS: every declared version normalizes to <version>`. That version
must be **newer** than the installed one from step 1. If it is the same, `claude plugin update` will
skip it (step 4). An end user stops and reports here; only a maintainer cutting the release bumps
it (see the [checklist](#appendix--cutting-the-new-version-directory-source)).

Then remove the worktree (`git -C <PLUGIN_DIR> worktree remove <TEST_TREE>`).

**2c. Pull.** This changes the code and the installed `DISCLAIMER.md` that every running session
uses, so record the [settings baseline](#confirm-existing-settings-with-the-user--both-hosts) first:

```bash
git -C <PLUGIN_DIR> merge --ff-only '@{upstream}'
git -C <PLUGIN_DIR> rev-parse HEAD '@{upstream}'
```

**Check:** both hashes are equal, and equal to the commit you tested in 2b.

For the full backend matrix (Claude Code × codex/pi/claude and native Codex), see
[*Testing an update across every supported backend*](#testing-an-update-across-every-supported-backend).

## Step 3 — make the new version visible to the host

Both sources: refresh the marketplace so the host re-reads the manifest.

```bash
claude plugin marketplace update reviewer-agent
```

**Check:** prints `Successfully updated marketplace: reviewer-agent`. For a Directory source this
re-reads the on-disk `.claude-plugin/marketplace.json`; if you bumped the version, the new number is
now what the marketplace advertises.

## Step 4 — update the plugin

```bash
claude plugin update reviewer-agent
```

**Check:** it reports `updated from <old> to <new>`. That is success.

**If instead it prints `already at the latest version`:** the installed version string matches the
marketplace's. That does **not** prove the installed files are current. For either source, newer
commits under an unchanged version are skipped. The update compared the string, saw no difference,
and did nothing. For a Directory source you are publishing, bump the version (appendix) and repeat
from step 3. If you cannot bump it, or you are deliberately installing newer commits under the
same label, force a re-copy by reinstalling at the same version:

```bash
claude plugin uninstall reviewer-agent
claude plugin install reviewer-agent@reviewer-agent
```

Uninstall-then-install preserves `~/.claude/reviewer-agent/` state, so the repo stays enabled and
the risk receipt survives — but the repository is briefly un-gated between the two commands, so do
it deliberately and prefer the version bump.

A same-version reinstall replaces the files **in place**, in the same `…/<version>/` cache
directory. Sessions already running that version keep the hook list they started with, but from
then on they run the new scripts until they restart. Sessions on older versions are unaffected:
their cache directories stay on disk.

**Optional check — confirm the copy is actually current.** First print what was installed:

```bash
python3 -c "import json,os;e=json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['reviewer-agent@reviewer-agent'][0];print(e['version'], e['installPath'], e['gitCommitSha'])"
```

For a **Directory** source, compare a file you know changed against the installed copy:

```bash
diff -q <PLUGIN_DIR>/stop_hook.py "$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['reviewer-agent@reviewer-agent'][0]['installPath'])")/stop_hook.py" && echo "IN SYNC"
```

For a **GitHub** source there is no local checkout. Check that `gitCommitSha` is the commit you
expect, i.e. the head of `main` on GitHub. For a full comparison from any clone that has that
commit, unpack it into an empty directory and diff it against the `installPath` printed above:

```bash
mkdir <empty-dir>
git -C <any-clone> archive <gitCommitSha> | tar -x -C <empty-dir>
diff -rq <empty-dir> <installPath>
```

**Check:** the version is the new one and the files match. For a Directory source, `stop_hook.py`
reports `IN SYNC`. For a GitHub source, `gitCommitSha` is the expected commit, and the full diff
lists only the host's own `.in_use` marker and `__pycache__`.

## Step 5 — restart Claude Code

`claude plugin update` prints `Restart to apply changes` for a reason: **the hook list is
snapshotted per session.** After a version bump, the new code sits in a new `…/<new>/` directory,
so the session you are in keeps running the old code until Claude Code restarts. The exception is a
same-version reinstall (step 4): it replaces the scripts in place. A running session then keeps its
old hook list, but each hook runs the new script files from disk, so commits in that session may be
gated by the new scripts before the restart. Restart to make the hook list and the scripts match.

Ask the operator to restart Claude Code **in the target repository** (`cd <TARGET_REPO> && claude`),
not in the checkout the update ran from. The settings walkthrough's enablement screens and the
step 7 commit check happen there, and an `on acceptRisk` typed in another repository records a
receipt for that repository instead. Before yielding, record the checkpoint (target, versions
before/after, completed checks, the settings baseline, next step) in the existing handoff file:
the restarted session does not have this conversation.

**Check (after restart):** the `installed_plugins.json` line from step 1 shows the new version (for a
Directory source, `claude plugin list` alone does not prove this; see step 1). The accepted-risk receipt and
per-repo enablement are unchanged, because state was never touched.

Then run the [settings walkthrough](#confirm-existing-settings-with-the-user--both-hosts), starting
at step 1 of 5, before step 6.

If `stopContinue` and `stopContinueWatchdog` are active for a repository, the watchdog recovery cron
needs no re-arming by hand. Whether it survives the restart depends on the host. On Claude Code
2.1.302 (2026-10-17), a session resumed after the restart still listed its watchdog cron, with the
same job id, and it kept ticking. On a host that does not carry crons across a restart, the cron is
gone (it lives in the host session's memory). **`SessionStart` does not bring it back**: its arm
instruction is not delivered on a resumed session.

What recovers a lost cron is tick liveness, and the timing is **bounded rather than immediate**. The
`tick_seen` marker is on disk and survives the restart, so a prompt only triggers the re-arm once
that marker is older than two watchdog intervals (14 minutes at the default 7). Restart shortly
after a tick and the marker still looks fresh: the cron is genuinely gone, yet the next prompt will
*not* re-arm it. So:

- exactly one task in `CronList` after a restart is expected, and so is an empty one;
- so is an empty one after your first prompt, if that prompt came soon after the last tick;
- within about two intervals a prompt re-arms it, and `CronList` then shows exactly one task.

If it is still missing well past that, see
[`docs/plans/watchdog_auto_shutoff.md`](docs/plans/watchdog_auto_shutoff.md) — the arm can also be
requested by hand from the agent with `CronList`/`CronCreate`.

## Step 6 — Pi backend only: refresh the extension

**Skip this unless the resolved backend is `pi`.** A plugin update does not touch
`~/.pi/agent/extensions/git-read-only/`, so if the bundled extension changed in this release the
installed copy is now stale. Because the reviewer withholds bash from Pi and reads the repository
solely through this extension, a stale extension behind the fail-closed gate can block commits.

The extension source is `<PLUGIN_DIR>/pi-extensions/git-read-only`. For a **Directory** source that
is your checkout (step 2). A **GitHub** source has no checkout — use the installed plugin directory
instead, i.e. the `installPath` from step 4's optional check
(`~/.claude/plugins/cache/reviewer-agent/…/<version>/pi-extensions/git-read-only`); substitute it
for `<PLUGIN_DIR>` in the commands below.

First see whether it actually changed; only replace it if so:

```bash
diff -rq <PLUGIN_DIR>/pi-extensions/git-read-only ~/.pi/agent/extensions/git-read-only \
  --exclude node_modules --exclude .git
```

If it differs, rebuild and replace it (an update *does* replace here — unlike a first install, which
refuses to clobber):

```bash
cd <PLUGIN_DIR>/pi-extensions/git-read-only
npm ci
node test/run.mjs                       # expect: 55 passed, 0 failed
rm -rf ~/.pi/agent/extensions/git-read-only
cp -R <PLUGIN_DIR>/pi-extensions/git-read-only ~/.pi/agent/extensions/
```

**Check:** `node test/run.mjs` passes before you copy, and the post-copy `diff -rq … --exclude
node_modules --exclude .git` is empty. The copy carries `node_modules/` (~131 MB), which is
intended: the installed extension needs its dependencies.

## Step 7 — prove the gate still gates

Do not declare success on configuration alone. After the restart, confirm a reviewer verdict
appears on the next commit in a gated repository — an approval or a rejection both prove the new
hook fired. Do not manufacture a commit in the operator's repository; identify the first real
commit after the restart as the end-to-end check unless the operator asks for an immediate proof
and agrees on a harmless test commit.

**Check:** the commit is audited (allowed or blocked with reasons). If it completes with no
reviewer output at all, the hook did not run: confirm the restart happened and `claude plugin list`
shows the new version.

---

## The Codex-native host

The Codex plugin is a separate package with its own procedure, summarised here; the authoritative
copy is [`plugins/reviewer-agent-codex/README.md`](plugins/reviewer-agent-codex/README.md#update-disable-and-uninstall).
First record the [settings baseline](#confirm-existing-settings-with-the-user--both-hosts), and run
that walkthrough after the trust and restart checkpoint below; this applies even when entering
directly at this Codex section.

Codex resolves cached plugins by version string. Published candidates already contain their
version and `+codex.<timestamp>` build metadata; keep those committed values when updating.
The supported local installer also refreshes the canonical cache at an unchanged label while
preserving displaced live cache directories for running sessions. Refreshing the cachebuster is
a maintainer's release-preparation step, not an end-user update step.

Before updating, record the installed version, the target of
`~/plugins/reviewer-agent-codex`, and the existing feature settings and risk receipts. Preserve
unrelated plugin/configuration entries. If the checkout has local edits, preserve them and use a
separate clean checkout of the selected commit; do not pull over them. For a pinned candidate,
record its full SHA and use that exact checkout or its verified permanent export throughout.
Do not pull a pinned candidate or an export; an export has no Git metadata.

Use separate Python paths: `<TEST_VENV>/bin/python` is the test interpreter with development
dependencies; `<RUNTIME_PYTHON>` is an absolute path to a **permanent Python 3.9+ interpreter**.
The installer saves its interpreter path for hooks and MCP. A temporary test venv must not become
that runtime, including accidentally through an activated venv's `python3`.

```bash
git -C <PLUGIN_DIR> pull --ff-only               # only a clean, unpinned tracked checkout
<TEST_VENV>/bin/python -m pip install -r <PLUGIN_DIR>/requirements-dev.txt
(cd <PLUGIN_DIR>/plugins/reviewer-agent-codex && <TEST_VENV>/bin/python -m pytest -p no:cacheprovider)
<RUNTIME_PYTHON> <PLUGIN_DIR>/plugins/reviewer-agent-codex/scripts/install_local.py
```

If the package's source path changes, the installer deliberately refuses to overwrite the
existing source symlink. Before the last command, follow
[Changing the existing source directory](plugins/reviewer-agent-codex/README.md#changing-the-existing-source-directory).
That procedure invokes the same installer while preserving the previous source and recording
the link target for recovery. Do not uninstall merely to move the source.

**Check before restart:** record the installer's actual cache path and version; compare every
package file in that cache with the verified source, excluding generated `__pycache__` files.
Confirm the saved `~/.codex/reviewer-agent-codex/python-path` is permanent, the previous live cache
still exists, and feature settings, risk receipts and unrelated configuration are unchanged.
Initialize a fresh MCP process using the installed `.mcp.json` command, arguments and working
directory: its version must match the candidate and its tool inventory must be exactly
`review_commit`, `stop_hook`, and `interrupt_stop`. Initialization and `tools/list` are read-only;
do not manually call the lifecycle tools. This checks the installed launcher, not whether the
current developer host has reloaded it.

After installation, follow the [host-specific trust and restart checkpoint](INSTALL_WITH_AGENT.md#4-hook-trust-and-host-restart).
For terminal use, restart Codex in the target repository and inspect `/hooks`; trust only entries
requiring approval. For GUI use, complete that CLI inspection, then fully quit and restart the GUI
in the target repository. `/hooks` is not a GUI command. Already-trusted entries need no repeated
trust action. State under `~/.codex/reviewer-agent-codex/` survives, so existing features and a
current receipt normally survive. A changed disclaimer or corrected
repository identity can require new acceptance; see the package README. New disposable targets
also need their own operator acceptance.

The commands above install test dependencies and run the native plugin suite in an explicit
package-directory subshell before installation. Use an available Python 3.9+ interpreter
in the test environment, and the separate permanent interpreter for installation.
The root suite does not collect it. Its backend and installer regressions exercise review launches
during cache parking, after restoration, and after cache eviction without model calls.

Older running servers, including `1.2.1-beta.5`, can fail to launch `_process_guard.py` while the
installer temporarily parks their cache directory. This can occur during a successful documented
update; the error alone does not establish an incorrect installation. A completed version-changing
update restores that path, whereas bypassing the installer can leave it absent. Complete the
restart before treating the update as verified, and distinguish a `failed_open` stop result from
a supervisor approval.

**Check:** a new thread offers the `reviewer-agent-codex` tools; `/hooks` shows the hooks as
trusted; and the MCP server advertises the new version (the `codex-native` matrix scenario asserts
exactly this — see below).

---

## Testing an update across every supported backend

The claimed support surface is five host×backend combinations, and the repository drives them with
one runner, `tools/release_matrix.py`, against **real hosts and real backends** in throwaway state
(nothing it does installs, uninstalls or reconfigures a host):

| scenario | host | backend |
|---|---|---|
| `claude-codex` | Claude Code | codex |
| `claude-pi` | Claude Code | pi |
| `claude-claude` | Claude Code | claude (the operator's own subscription quota) |
| `codex-native` | Codex | codex |
| `distribution` | — | packaging / manifest parity |

```bash
python3 tools/release_matrix.py --list                      # the scenarios, and the manual ones
python3 tools/release_matrix.py --all --stub                # fast; no model calls
python3 tools/release_matrix.py --all --keep --report /tmp/matrix.md   # real calls; retain evidence
python3 tools/release_matrix.py --scenario claude-pi        # one combination
```

Three scenarios — `claude-pi`, `claude-claude` and `codex-native` — **cannot be stubbed** (the
stand-in speaks only the codex argv/stdout shape), so a real run is required to prove them.
`claude-claude` spends the operator's Claude subscription quota. Prefer `--stub` for a fast smoke
check and reserve `--all` (real) for validating an actual release.

The runner also prints **six scenarios it cannot script** — they need a person in a real host:
pressing Escape mid-review, restarting to prove persistence, a clean-profile install, native-Codex
shell-commit redirection, the `AskUserQuestion` reroute in a real interactive session, and uninstall-survival of unrelated
plugins. Report these as "requires a human" rather than claiming them green.

Retain the workspace printed by `--keep` and save a sanitized summary separately from private
reviewer transcripts. The current matrix's unfinished-Stop assertion can also accept a diagnostic
containing `stopContinue`; inspect the actual `decision: block`, directive and successful reviewer
outcome before counting that check as acceptance. A fail-open diagnostic is not continuation or
approval. See the [Linux acceptance runbook](docs/LINUX_RELEASE_ACCEPTANCE.md) for host evidence.

Alongside the matrix, the always-run gates for any release are the two unit suites plus the
structural and parity checks — see [`CONTRIBUTING.md`](CONTRIBUTING.md#running-the-suites).

## Testing an unreleased build without a version bump

Usually keep the existing version while diagnosing unreleased changes. For final acceptance,
maintainers may first freeze the next release's metadata so the last platform test exercises
the intended version; a version bump alone does not publish a GitHub release. There are two
ways to try unreleased commits without changing their version locally.

**Commits already pushed to `main` (GitHub source):** stay on the GitHub marketplace and reinstall
at the same label. `claude plugin update` alone copies nothing here (see
[above](#the-one-thing-that-trips-up-every-local-update)).

```bash
claude plugin marketplace update reviewer-agent
claude plugin uninstall reviewer-agent
claude plugin install reviewer-agent@reviewer-agent
```

Run the last two back to back with no commit in progress; the gate is not guaranteed between them.
Confirm `gitCommitSha` with step 4's optional check, then restart (step 5). Nothing needs undoing
afterwards: the next release installs normally through `claude plugin update`.

**Commits not pushed, or a build you want to pin:** install an **export** of them at the version
label they already carry, test, then return to the GitHub marketplace.

1. **Export to a permanent directory**. Replace `<EXPORT_DIR>` with your chosen absolute
   destination, named for the commit so a later export can sit beside it.
   The export copies the **working tree**, so run it from a clean checkout of that commit (a
   `git worktree add <dir> <sha>` is the simplest), and confirm `git status --short` prints
   nothing first. Otherwise you test edits the named commit does not contain.

   ```bash
   git -C <clean-checkout> status --short          # must print nothing
   python3 <clean-checkout>/tools/release_export.py --target <EXPORT_DIR>
   ```

   **Never use a scratchpad or anything under `/tmp`.** A Directory marketplace runs the plugin's
   hooks from that directory, not from a cached copy. macOS deletes `/tmp` files unused for 3 days.
   On 2026-10-07 that happened to a test build: every hook then exited 127, which Claude Code
   treats as non-blocking, so stop supervision stopped and **the commit gate failed open**. The
   export refuses a non-empty target, so a refresh is a new directory, not an overwrite.

2. **Point the marketplace at the export and install from it:**

   ```bash
   claude plugin marketplace remove reviewer-agent
   claude plugin marketplace add <EXPORT_DIR>
   claude plugin install reviewer-agent@reviewer-agent
   ```

   Run these back to back with no commit in progress; the gate is not guaranteed between them.
   Then restart the sessions that should test it (step 5).

3. **Test**, then **return to GitHub** the same way:

   ```bash
   claude plugin marketplace remove reviewer-agent
   claude plugin marketplace add rladr-org/reviewer-agent
   claude plugin install reviewer-agent@reviewer-agent
   ```

   Restart again. Keep the export directory until no session still runs from it; a running
   session keeps the hook path it started with.

---

## Appendix — cutting the new version (Directory source)

A local update is invisible until the version changes, so bumping the version *is* the update
trigger. Identity lives in **one** place, `tools/release_versions.py`, and is checked against every
file that declares it. To cut a new version:

1. Edit `RELEASE` and `PEP440_RELEASE` in `tools/release_versions.py` (e.g. `1.2.0-beta.1` /
   `1.2.0b1` → `1.2.0-beta.2` / `1.2.0b2`). `TAG` derives from `RELEASE`.
2. Update every declared version to match — the parity checker enumerates all seven:
   `.claude-plugin/plugin.json`, both entries in `.claude-plugin/marketplace.json`
   (`metadata.version` **and** the plugin entry), and, in
   `plugins/reviewer-agent-codex/`: `.codex-plugin/plugin.json` (base only), `pyproject.toml` and
   `reviewer_codex/__init__.py` (PEP 440), and `reviewer_codex/mcp_server.py` (SemVer).
3. Refresh the Codex cachebuster — never edit it by hand:
   `python3 tools/release_cachebuster.py`.
4. Verify parity: `python3 tools/release_versions.py` prints `PASS`, and
   `python3 -m pytest tests/test_release_metadata.py -q` is green.
5. Add a `## [<new version>] — unreleased` section to `CHANGELOG.md`.
6. Then run the update procedure above (steps 3–5), and, for a release you can trust, commit first
   so `gitCommitSha` matches what is installed.

The matrix runner reads `release_versions.RELEASE` directly, so there is no separate version
literal to bump there. `clientInfo.version` in `reviewer_codex/host_config.py` is deliberately
**not** part of the release identity (it names the installer client in a handshake) and is exempt
from the parity check by design.

---

## Report when done

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

Then report the **installed version before and after**, the **marketplace source** (GitHub vs
Directory), and — for a Directory source — **whether you had to bump the version** to make the
update take. Then state plainly: whether the restart happened, whether state survived (it should
have; verify receipt validity before assuming enablement needs no action), the effective settings
and sources before/after, the user's keep/change decision and any unresolved mismatch, whether
the Pi extension needed refreshing, which
matrix scenarios you ran and their result, and which human-only scenarios remain outstanding. If
you stopped early, say at which step and why.
