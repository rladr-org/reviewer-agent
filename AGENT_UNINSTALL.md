# Uninstalling reviewer-agent unattended


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

A procedure for an **agent** told to remove this project from a machine. It mirrors
[`AGENT_INSTALL.md`](AGENT_INSTALL.md): every step has a command and a check, and you do not proceed
past a failed check.

Choose the section for the active host. For native Codex, follow
[Codex uninstall and reinstall](#codex-uninstall-and-reinstall) below and stop at its handoff.
The remaining teardown steps apply only to **Claude Code**, either its current plugin or a legacy
standalone install. Do not run Claude teardown commands for a Codex installation.

If you are a human, README.md's [*Update, disable and uninstall (Claude
Code)*](README.md#update-disable-and-uninstall-claude-code) is shorter.

---

## Codex uninstall and reinstall

Use the documented uninstaller directly, then verify removal before starting a separate install.
Do not create a temporary script that combines uninstall, checks and reinstall, or put reinstall
in an unconditional cleanup/finally block. A failed check must remain visible and stop the
procedure. If the instructions cannot be followed, record the gap and correct this document
before continuing; an improvised wrapper does not validate these instructions.

### C1 — identify the installation and preserve a baseline

Confirm whether the user wants a full uninstall or a deliberate uninstall/reinstall test, and
whether retained settings and history should survive. An
explicit request for that test already authorizes both steps; do not ask again. If they only want
one repository's reviews stopped, use the documented per-repository controls instead.
Offer two removal modes: **uninstall and keep settings/history** (the default), or **full
uninstall**, removing both features' plugin configuration and history across all repositories.
Full uninstall is available whether or not the operator plans to reinstall. If they explicitly
request it, follow [C3a](#c3a--full-uninstall-remove-plugin-configuration-and-history) after the removal restart.
That request authorizes the reset; do not ask for the same permission again.

Identify `<TARGET_REPO>`, `<INSTALLER_REPO>` (the permanent combined checkout or verified export),
the installed cache path/version, and a permanent Python 3.9+ `<RUNTIME_PYTHON>`. Record the source
link and saved interpreter:

```bash
readlink "$HOME/plugins/reviewer-agent-codex"
cat "$HOME/.codex/reviewer-agent-codex/python-path"
```

Run the uninstaller from that permanent source, not from the installed cache it removes. Preserve
the source for reinstall; do not pull a pinned candidate, edit its version or delete it. Verify
that the source link belongs to this plugin; an unexpected target must not be removed by hand.
Record effective model/effort/timeouts, rotation/retention, repository feature flags and receipt
validity. Keep the user's project brief unchanged. Ensure no review or commit is active.

Explain the external paths before requesting normal host approval: this plugin's installed entry,
canonical cache under `~/.codex/plugins/cache/`, entry in `~/.agents/plugins/marketplace.json`,
`~/plugins/reviewer-agent-codex` source link, and saved `python-path` will be removed. Retained
state, other plugins, unrelated configuration and the permanent source must survive this initial
removal. For an explicitly requested full reset, retained state is removed in C3a after restart.

Back up the existing files privately. The command prints the path; use that exact absolute path
as `<BACKUP>` in later steps, since shell variables do not persist between tool calls.

```bash
set -e
umask 077
reviewer_state="${REVIEWER_CODEX_STATE_DIR:-$HOME/.codex/reviewer-agent-codex}"
reviewer_backup="$HOME/reviewer-agent-backups/codex-uninstall-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$reviewer_backup"
if [ -f "$HOME/.codex/config.toml" ]; then
  cp "$HOME/.codex/config.toml" "$reviewer_backup/config.toml"
fi
if [ -f "$HOME/.agents/plugins/marketplace.json" ]; then
  cp "$HOME/.agents/plugins/marketplace.json" "$reviewer_backup/marketplace.json"
fi
if [ -d "$reviewer_state" ]; then
  cp -R "$reviewer_state" "$reviewer_backup/state"
fi
if [ -d "$HOME/.codex/plugins/cache" ]; then
  cp -R "$HOME/.codex/plugins/cache" "$reviewer_backup/plugin-cache"
fi
readlink "$HOME/plugins/reviewer-agent-codex" > "$reviewer_backup/source-link.txt"
printf 'Backup: %s\nState: %s\n' "$reviewer_backup" "$reviewer_state"
```

**Check:** each original file/directory has its backup. Record absent originals as absent, not as
backup failures. Keep raw configuration and reviewed-code history private. For a procedure test,
retain snapshots at each phase rather than only a boolean comparison result.

### C2 — run the documented uninstaller

Invoke this command directly and retain its actual output and exit status:

```bash
"<RUNTIME_PYTHON>" "<INSTALLER_REPO>/plugins/reviewer-agent-codex/scripts/uninstall_local.py"
```

Do not use `--purge-state` during this initial removal from the still-loaded host. Read both the removed items and warnings; exit zero alone is not a
complete removal check. The uninstaller deliberately reports one manual edit: removing
`mcp__reviewer_agent` from `features.code_mode.direct_only_tool_namespaces`. Leave other entries
alone. For a reinstall test, retain this entry because installation still needs it. For permanent
removal, report it as a remaining operator step if it is not removed.

**Checkpoint:** save the uninstalled-state evidence before reinstalling. Verify:

- The intended installed plugin entry, canonical plugin cache and marketplace entry are gone.
  A marketplace listing can still advertise an available plugin; availability is not installation.
- The source link is gone when it pointed to the chosen source; an unexpected link is preserved
  and reported. The fixed `~/.codex/reviewer-agent-codex/python-path` file is removed intentionally.
- Persistent settings, project flags, risk receipts, retained history and project briefs match
  the baseline. SessionEnd may clean transient session data; distinguish that from lost retained
  data. Do not mistake the intentional removal of `python-path` for a state-purge failure.
- Unrelated plugins, marketplace entries and configuration are unchanged. Compare parsed
  configuration values, not TOML formatting or table order. Removing this plugin's own entry is
  expected; an absent parent table and an empty parent table are equivalent only when that table
  contains no remaining settings. Do not ignore a nonempty table or unrelated key difference.

Keep the raw before/after configurations in `<BACKUP>` and report only relevant differences. If a
check fails, report the exact discrepancy and stop before reinstalling. If restoring service is
necessary, identify it explicitly as recovery, not a passing uninstall test. If the old host's
cached hooks prevent further shell checks after removal, perform the C3 restart first and finish
this checkpoint in that fresh host before reinstalling. Record those checks as pending until then.

### C3 — restart and verify removal

Running hosts can retain old hook definitions and an already connected reviewer server. Do not
interpret that session as proof of a clean uninstall. Ask the operator to exit the old host and
start a fresh interactive Codex session in `<TARGET_REPO>`. GUI users must also fully quit and
restart the GUI. The permanent source and backup must remain on disk.

**Check in the fresh host:** `/hooks` in the interactive CLI no longer lists this plugin's active
hooks, its reviewer MCP server is no longer connected, and ordinary shell work succeeds without
missing-plugin errors. Preserve unrelated hooks and MCP servers. Do not manufacture an ungated
commit in a real project to prove removal. If removal or the host restart is unverified, report
it as pending; do not silently reinstall and call the entire cycle successful.

### C3a — full uninstall: remove plugin configuration and history

Run this only when the operator has explicitly requested full removal including this plugin's
settings and history. It removes commit review and Stop supervision state for **all repositories in the resolved
reviewer state directory**, not just the current target. Preserve Codex authentication, unrelated
configuration, project source and hand-written `reviewerAgent.md` briefs. Backups outside the
active plugin directories may remain; disclose their location and that they still contain the
saved history. If the operator also wants those backups deleted, that is a separate explicit
choice. Do not restore their settings during a fresh-install test.

Before the initial uninstall, extend C1's private baseline with the plugin's saved hook-trust
entries in `config.toml`, direct-tool namespace and any parked cache siblings named
`.reviewer-agent-codex-live-*`. Verify their ownership by checking their plugin manifests;
an unexpected directory is not safe to remove just because its name looks similar. Record any
`REVIEWER_CODEX_*` environment overrides that could affect defaults, without exposing secrets.
Keep backups of each plugin-owned directory that will be removed.

After C3 confirms that the fresh host no longer loads the plugin, ensure other host sessions
using this plugin have ended too. A shared host daemon can retain an idle reviewer MCP process
even after the terminal session restarts. If one remains, verify its exact plugin entrypoint and
source/cache directory, and confirm it has no active review or child process before terminating
only that leftover reviewer server. Do not terminate unrelated Codex processes. Record the
cleanup; an active reviewer must finish or be explicitly cancelled before proceeding.
Then run the official uninstaller again, this time with its
explicit state-purge option, from the same permanent source:

```bash
"<RUNTIME_PYTHON>" "<INSTALLER_REPO>/plugins/reviewer-agent-codex/scripts/uninstall_local.py" --purge-state
```

Verify that the resolved state directory is absent, including settings, flags, risk receipts,
review history and reviewer/Stop session records. A live old host can recreate it; investigate
reappearance before claiming a reset. Check the fixed default interpreter-selection path too
when a custom `REVIEWER_CODEX_STATE_DIR` is used. Do not delete a shared custom directory without
first establishing that its contents belong entirely to this plugin.

Remove only this plugin's remaining configuration: `mcp__reviewer_agent` from
`features.code_mode.direct_only_tool_namespaces`, and hook-state entries whose exact plugin ID
prefix is `reviewer-agent-codex@<MARKETPLACE>:`. Preserve other namespaces and hook-trust entries.
Use a targeted edit or the host's configuration interface, retaining raw before/after snapshots
and checking parsed values; do not replace the entire configuration with a template. After all
old plugin sessions have ended, remove the previously identified parked plugin caches. Keep
the permanent installer source and private backups outside active cache/state locations.

**Check:** no plugin registration, source link, canonical/parked plugin cache, saved hook trust,
direct namespace, settings, flags, receipts or retained review history remains in active
locations. Unrelated configuration and authentication must match the baseline. Project briefs
are intentionally preserved. Host-wide Codex conversation logs are not plugin-managed review
history and must not be purged. If environment overrides remain, report them; do not describe
the resulting settings as untouched defaults. Save evidence of this empty plugin baseline
before any reinstall. For permanent removal, report completion here and skip C4. If reinstalling
to test first-time setup, this establishes an empty plugin baseline in an existing Codex setup,
not installation of Codex on a clean operating-system account.

### C4 — reinstall through the initial-install guide, when requested

After the uninstall checks pass, start
[`INSTALL_WITH_AGENT.md`](INSTALL_WITH_AGENT.md#procedure-for-the-installing-agent) at step 1.
Use its repository identification, prerequisite checks, supported installer and operator
hook-trust/restart checkpoint, followed by the settings/enablement screens for each feature and
real post-install checks. Do not replace this with an
update command or a combined uninstall/reinstall helper.

Preserved state makes this a reinstall, not a clean-profile installation. Existing current risk
receipts need no repeat acceptance and enabled/disabled choices must be preserved. To exercise
first-time setup, use a separate disposable target without a receipt and open the developer host
there; do not purge existing state without the explicit full-removal request required by C3a.
A new target tests first-time repository setup,
not a clean host profile. Record these as distinct coverage. The explicitly authorized C3a reset
is the exception: it tests installation without prior plugin configuration or acceptance receipts.

After a state-preserving reinstall, compare files and effective settings with the baseline. After
C3a, verify fresh defaults and disabled features before operator choices; old settings and risk
acceptance must not return. Complete the operator
restart, then verify a real gated commit and automatic Stop in the intended enabled test target.
Report uninstall, reinstall, restart and host checks separately. The rest of this document is for
Claude Code and must not be run for native Codex.

## Read this before running anything

**Most requests do NOT need a full uninstall — confirm intent first.** Three narrower actions cover
almost every real need, and none removes the tool:

- **Stop gating one repository:** the operator runs `/reviewerAgent off` (and `/stopContinue off`).
  Nothing is deleted; the flag is per canonical repository root. Prefer this.
- **Disable the plugin everywhere but keep it installed:** `claude plugin disable reviewer-agent`.
- **Purge one project's review record:** `/reviewerHistory clear`.

A full uninstall removes the gate for **every** repository on the machine. If the operator asked only
to stop reviews on one project, do the disable above and stop.

**The one real hazard: the Bash gate may be chained inside the operator's own approval hook.** A
standalone install commonly registers a single `PreToolUse` (Bash) wrapper at
`~/.claude/hooks/bash_gate.py` that runs, in order, a **general preflight hook — often the operator's
OWN bash-approval hook** — and then the reviewerAgent `commit_hook.py`, the latter **fail-closed**. If
you delete `~/.claude/reviewerAgent/` while `bash_gate.py` still references `commit_hook.py`, the
wrapper tries to exec a missing script on **every Bash command** and, being fail-closed, can block
**all** Bash — not just commits. So you must **de-chain reviewerAgent from `bash_gate.py` before
deleting the directory**, and you must **preserve any non-reviewer preflight hook it chains**. A
half-finished teardown of a fail-closed gate is worse than none.

**Hooks are snapshotted per session.** Nothing here takes effect in a session that is already
running. The operator must **restart Claude Code** afterward, then verify with `/hooks`. The restart
is operator-only.

**Stop and report, do not improvise, if:** `~/.claude/settings.json` is unwritable; you cannot tell
which preflight entry in `bash_gate.py` is the operator's and which is reviewerAgent's; or any check
below does not produce the stated result.

---

## Step 0 — back up first

Uninstalling is only reversible if the current configuration is saved. **Resolve the state directory
first** — the runtime honors `REVIEWER_STATE_DIR`, then the legacy `REVIEWER_HOME` alias, then the
default `~/.claude/reviewer-agent` — then copy every surface to one timestamped directory:

```bash
STATE="${REVIEWER_STATE_DIR:-${REVIEWER_HOME:-$HOME/.claude/reviewer-agent}}"
BK="$HOME/.claude/reviewer-agent.uninstall-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BK"
cp    ~/.claude/settings.json  "$BK"/settings.json   2>/dev/null
cp -R ~/.claude/reviewerAgent  "$BK"/reviewerAgent   2>/dev/null   # standalone code (if present)
cp -R "$STATE"                 "$BK"/state           2>/dev/null   # flags, ids, verdicts, history
cp -R ~/.claude/hooks          "$BK"/hooks           2>/dev/null   # bash_gate.py + any preflight
echo "backup at $BK (state resolved to $STATE)"
```

The command prints the backup path; use that exact absolute path as `<BACKUP>` in later steps, since
shell variables do not persist between tool calls.

**Check:** `<BACKUP>` exists and contains `settings.json`; `state/`, `reviewerAgent/` and `hooks/` are
present if they existed. Reviewer scratch (Step 3) is transient and is deleted, not backed up.

## Step 1 — detect the install shape

```bash
rg 'reviewerAgent|bash_gate\.py' ~/.claude/settings.json
claude plugin list | rg reviewer-agent || true
```

- Commands under the **plugin** directory, or `claude plugin list` shows `reviewer-agent` → **plugin
  install** → do Step 2P.
- Commands under `~/.claude/reviewerAgent/`, or a `~/.claude/hooks/bash_gate.py` wrapper → **legacy
  standalone** → do Step 2S.
- Both can appear at once on a machine mid-migration; do **both** teardowns.

## Step 2P — remove the plugin

```bash
claude plugin uninstall reviewer-agent
claude plugin marketplace remove reviewer-agent      # optional: only if nothing else uses it
```

The marketplace is unused once the `plugins` list in its `.claude-plugin/marketplace.json` names
only `reviewer-agent`. For a Folder source that file is in the folder `claude plugin marketplace
list` shows. Removing the marketplace leaves that folder on disk.

**Check:** `claude plugin list` no longer shows `reviewer-agent`. **If Step 1 also found standalone
artifacts** (commands under `~/.claude/reviewerAgent/`, or a `bash_gate.py` wrapper), do **Step 2S**
next — the plugin uninstall does not touch them, and leaving them causes duplicate/again-firing hooks.
Otherwise go to Step 3.

## Step 2S — tear down the legacy standalone (the delicate one)

**a. Inspect the Bash wrapper's chain** so you know what to keep:

```bash
rg -n 'default_chain|preflight|commit_hook|closed|open' ~/.claude/hooks/bash_gate.py
```

Identify the two entries: the **preflight** hook (frequently the operator's own bash-approval hook —
**keep it**) and the reviewerAgent `commit_hook.py` (**remove it**).

**b. De-chain reviewerAgent**, one of:
- edit `bash_gate.py` so its chain no longer lists `commit_hook.py`, leaving the preflight entry; **or**
- if the preflight is the only thing left, repoint the `PreToolUse` (Bash) hook in
  `~/.claude/settings.json` directly at that preflight script and remove the `bash_gate.py` wrapper.

If instead `PreToolUse` (Bash) points **directly** at `commit_hook.py` (no wrapper), just remove that
one hook entry. **Never remove the operator's own preflight/approval hook.**

**c. Remove the other standalone entries** from `~/.claude/settings.json`: the `PreToolUse`
(`askq_reroute_hook.py`, matcher `AskUserQuestion`), `SessionStart` (`session_start_hook.py`) and
`Stop` (`stop_hook.py`) hooks that point under `~/.claude/reviewerAgent/`.

**Check — BEFORE deleting anything:**

```bash
rg 'reviewerAgent' ~/.claude/settings.json      # expect: no command under ~/.claude/reviewerAgent/
rg 'commit_hook'   ~/.claude/hooks/bash_gate.py 2>/dev/null   # expect: nothing
```

**d. Delete the standalone code:**

```bash
rm -rf ~/.claude/reviewerAgent
```

**Check:** the operator's preflight/approval hook is intact (a normal Bash command is not blocked by a
missing-script fail-closed error), and `rg reviewerAgent ~/.claude/settings.json` is empty.

**e. Remove the standalone's user-scoped skill and command shortcuts.** The installer drops
copies under `~/.claude/commands/` and `~/.claude/skills/` that keep appearing in the slash-command
completion menu after `~/.claude/reviewerAgent/` is gone, each pointing at the now-deleted
`toggle.py` (they error on invocation, and they duplicate every entry the plugin registers
after a later reinstall):

```bash
rm -f ~/.claude/commands/reviewerAgent.md \
      ~/.claude/commands/reviewerDirective.md \
      ~/.claude/commands/reviewerModel.md \
      ~/.claude/commands/stopContinue.md \
      ~/.claude/commands/stopContinueWatchdog.md
rm -rf ~/.claude/skills/reviewerAgent
```

**Check:** `rg reviewerAgent ~/.claude/commands ~/.claude/skills 2>/dev/null` is empty. Do NOT
touch anything else under those directories — they hold the operator's own skills and commands.

## Step 3 — remove leftovers (optional; the operator's call)

Uninstalling the code does not remove these. Do each only if the operator wants it gone.

- **Reviewer scratch (codex backend) — do this even on an otherwise "keep everything" uninstall, but
  only once the reviewer is QUIESCENT.** Scratch is normally reclaimed by session rotation and the
  `SessionStart` sweep; once the plugin or standalone is gone, **neither runs again**, so any leftover
  `reviewer-agent-scratch` stays forever and keeps consuming disk. First make sure **no** Claude Code
  session is mid-audit — a running audit owns a live scratch dir under this root, and deleting it
  would break that session. Have the operator close other reviewer sessions first; do not run this
  while one is active. Then:
  - **Default location (no `REVIEWER_SCRATCH_ROOT`):** the managed root is a dedicated, tool-named
    directory (`reviewer-agent-scratch`), so removing it wholesale touches only tool data. The default
    follows Python's `tempfile.gettempdir()`, so check both the `$TMPDIR` base and `/tmp`:

    ```bash
    rm -rf "${TMPDIR:-/tmp}/reviewer-agent-scratch"
    rm -rf /tmp/reviewer-agent-scratch
    ```

  - **Custom `REVIEWER_SCRATCH_ROOT` dedicated to the reviewer** (nothing else writes there): remove
    it wholesale — `rm -rf "$REVIEWER_SCRATCH_ROOT"`.
  - **Custom `REVIEWER_SCRATCH_ROOT` that is a SHARED directory:** do **not** remove the root, but do
    not hand the problem to the operator either — they did not choose these names and have no way to
    recognise them. The names are **computable, not opaque**: every scratch directory is
    `<root>/_sanitize(<commit-reviewer session id>)`.

    Enumerate precisely, and note both details — getting either wrong in a shared root means
    deleting somebody else's directory:

    - The id files are named `<project-stem>.sess_<claude-session-id>` + a **role suffix** +
      `.codex` (`session_id_file` / `_project_session_stem` in `reviewer_state.py`). Match on the
      role suffix and the trailing `.codex`, **not** on a leading `sess_` — the project stem comes
      first. Each file holds its id as its only line.
    - Use **only the commit-reviewer role** (`.reviewer.codex`). The stop-supervisor role
      (`.stop.codex`) runs no commit audit and therefore owns **no** `commit_scratch_dir` entry, so
      sanitizing a stop id yields a name the tool never created — in a shared root that name may
      belong to something else entirely.

    Remove exactly those subdirectories and touch nothing else. Do this **before** removing state
    (the bullet below) — deleting state first destroys the only record of which directories were
    the tool's.

    Session rotation unlinks the id files of older generations, so scratch from a rotated generation
    may no longer be enumerable. Remove what is identifiable, then report how many were removed and
    that unidentifiable residue was left deliberately: residue in a shared directory is a far better
    outcome than deleting data belonging to something else, and that residue is what
    `sweep_orphan_scratch_dirs()` would have reclaimed on its mtime lease had the plugin stayed.

  **Check:** the dedicated managed scratch directory is gone, no unrelated data was removed, and any
  shared-root case was reported as incomplete rather than guessed at.
- **State.** `rm -rf "$STATE"` (the directory resolved in Step 0 — `REVIEWER_STATE_DIR`, else the
  legacy `REVIEWER_HOME`, else `~/.claude/reviewer-agent`) — every project's flags, reviewer/stop
  session ids, retained verdicts, and logs. Skip it to keep review history and per-repo enablement
  for a later reinstall. **Do this after the Step 4 restart, as for the plugin cache below:** the
  session that ran the uninstall still runs its snapshotted hooks, and its `Stop` hook recreates
  `"$STATE"/logs/reviewer.log` at the end of every turn. After the restart, check that no new
  entry was written since the restart before removing the directory.
- **Per-repository briefs.** Each protected repo has a hand-written `reviewerAgent.md`. Leave them
  unless the operator asks — they are not the tool's to delete.
- **The Pi extension**, only if `pi` was used: `rm -rf ~/.pi/agent/extensions/git-read-only`.
- **The `stopContinueWatchdog` recovery cron — remove this whenever `stopContinue` was used, even on
  a "keep everything" uninstall.** The cron lives in the host session, not on disk, and its tick
  tears itself down only when a flag file is missing or the session's `teardown_ready` marker is
  present. Keeping state (the bullet above) keeps those flag files, so **the self-teardown condition
  never fires and the tick runs forever against a plugin that is gone** — every few minutes, in
  every session that still has one armed. Uninstalling the code cannot cancel it: only the agent can,
  and only from the session that created it. Ask the agent to `CronList` and `CronDelete` every task
  whose prompt contains `stopContinueWatchdog`, or end the session that owns it.

  **Check:** `CronList` reports no marker-tagged task.
- **The plugin cache — `~/.claude/plugins/cache/reviewer-agent/`. DO THIS AFTER THE STEP 4 RESTART,
  NOT HERE.** `claude plugin uninstall` clears the registry entries in `installed_plugins.json` and
  `known_marketplaces.json`, but leaves **every installed version directory on disk**, one per
  version ever installed. On a machine that has followed a few releases this is tens of megabytes of
  dormant source — 88 MB across seven versions on the machine this was written from. Nothing reads it
  once the plugin is gone *and every session that knew about it has been restarted*.

  **Why the ordering is load-bearing:** hooks are snapshotted per session, and the snapshot stores an
  absolute path into this directory. A session that was running at uninstall time still tries to exec
  hooks from it, so removing it first makes **every** `UserPromptSubmit` and `Stop` in that session
  fail with `Plugin directory does not exist: …/reviewer-agent/<version>`, on every turn, until the
  operator restarts. Measured, not hypothetical. State, scratch and briefs are safe to remove here;
  this one is not.

  After the restart, prefer moving it to the Step 0 backup over deleting it, so a rollback is still
  possible:

  ```bash
  mv ~/.claude/plugins/cache/reviewer-agent "<BACKUP>"/plugin-cache
  ```

  **Check:** `~/.claude/plugins/cache/reviewer-agent` no longer exists, `claude plugin list` still
  shows every unrelated plugin, and no hook error appears on the next prompt.
- **Operator permission rules naming the plugin path.** `~/.claude/settings.json` may hold
  `permissions.allow` entries pointing at `~/.claude/plugins/cache/reviewer-agent/.../reviewer_cmd.py`,
  and a matching `permissions.deny` entry that refuses `..` paths under that same prefix.
  These are the **operator's own configuration**, not the tool's, and a dangling allow rule grants
  nothing once the path is gone — so this document does not remove them. Mention them and leave the
  decision to the operator.

## Step 4 — restart and verify (operator-only)

Work remains after the restart (these checks and the plugin-cache move in Step 3), so first record
where you are in `<BACKUP>/uninstall-status.txt`: the backup path, the install shape, the steps
done, what is still pending, and this session's id (`printenv CLAUDE_CODE_SESSION_ID`). Then give
the operator the exact command to come back to this session: quit Claude Code and, in the same
directory, run `claude --resume <session-id>` with the id filled in. Do not offer `claude
--continue`: it opens the most recent conversation in that directory, which need not be this
one. If the session cannot be resumed, a fresh one continues from that file; the operator gives
it the `<BACKUP>` path.

Ask the operator to **restart Claude Code** that way, then confirm:

- `/hooks` lists **no** reviewerAgent hooks (`session_start_hook.py`, `stop_hook.py`,
  `askq_reroute_hook.py`, or a `commit_hook.py` chained in `bash_gate.py`).
- A normal Bash command runs — the operator's own preflight/approval hook survived.
- A `git commit` in a previously gated repository is **no longer** intercepted by a reviewer.
- No `stopContinueWatchdog` tick arrives in the new session. One that keeps firing means a cron
  survived somewhere: ask the agent to `CronList`/`CronDelete` it (Step 3), because the plugin is no
  longer there to tear it down.

If a commit is still gated, a hook entry was missed: re-inspect `~/.claude/settings.json` and
`bash_gate.py` and repeat Step 2S.

---

## Report when done

State plainly: which install shape was found; exactly what was removed and what was intentionally
kept (reviewer scratch — and, for a shared custom scratch root, **how many owned directories you
removed** and whether any unidentifiable residue was left behind, never a request for the operator
to pick through it themselves — state, briefs, the timeout key, any preflight hook, the watchdog
cron, the plugin cache and any operator permission rules naming its path); the backup location from
Step 0; and the fact that a restart is required before the change is live. If you stopped early, say at which step
and why — a truthful partial report is more useful than a confident summary of a teardown that did not
finish.
