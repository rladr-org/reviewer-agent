# Uninstalling reviewer-agent unattended


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

A procedure for an **agent** told to remove this project from a machine. It mirrors
[`AGENT_INSTALL.md`](AGENT_INSTALL.md): every step has a command and a check, and you do not proceed
past a failed check.

This guide removes the **Claude Code** install — either the current plugin or a legacy standalone
install. For the native Codex plugin, follow *Update, disable and uninstall* in
[`plugins/reviewer-agent-codex/README.md`](plugins/reviewer-agent-codex/README.md) instead
(`scripts/uninstall_local.py`); it removes only that plugin's configuration.

If you are a human, README.md's [*Update, disable and uninstall (Claude
Code)*](README.md#update-disable-and-uninstall-claude-code) is shorter.

---

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

**Check:** `$BK` exists and contains `settings.json`; `state/`, `reviewerAgent/` and `hooks/` are
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

**c. Remove the other standalone entries** from `~/.claude/settings.json`: the `PostToolUse`
(`askq_answer_hook.py`), `SessionStart` (`session_start_hook.py`) and `Stop` (`stop_hook.py`) hooks
that point under `~/.claude/reviewerAgent/`.

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
  - **Custom `REVIEWER_SCRATCH_ROOT` that is a SHARED directory:** **stop — do not delete anything
    under it.** The tool's per-session subdirectories are named after sanitized reviewer session ids
    and cannot be distinguished by name from unrelated entries, so there is no ownership-safe automatic
    removal. Report scratch cleanup as **incomplete** and ask the operator to remove the reviewer's own
    scratch subdirectories by hand.

  **Check:** the dedicated managed scratch directory is gone, no unrelated data was removed, and any
  shared-root case was reported as incomplete rather than guessed at.
- **State.** `rm -rf "$STATE"` (the directory resolved in Step 0 — `REVIEWER_STATE_DIR`, else the
  legacy `REVIEWER_HOME`, else `~/.claude/reviewer-agent`) — every project's flags, reviewer/stop
  session ids, retained verdicts, and logs. Skip it to keep review history and per-repo enablement
  for a later reinstall.
- **The question timeout.** Enabling the reviewer added `askUserQuestionTimeout` to
  `~/.claude/settings.json`; it is a host setting the operator may still want. Delete the key by hand
  only if it is unwanted.
- **Per-repository briefs.** Each protected repo has a hand-written `reviewerAgent.md`. Leave them
  unless the operator asks — they are not the tool's to delete.
- **The Pi extension**, only if `pi` was used: `rm -rf ~/.pi/agent/extensions/git-read-only`.

## Step 4 — restart and verify (operator-only)

Ask the operator to **restart Claude Code**, then confirm:

- `/hooks` lists **no** reviewerAgent hooks (`session_start_hook.py`, `stop_hook.py`,
  `askq_answer_hook.py`, or a `commit_hook.py` chained in `bash_gate.py`).
- A normal Bash command runs — the operator's own preflight/approval hook survived.
- A `git commit` in a previously gated repository is **no longer** intercepted by a reviewer.

If a commit is still gated, a hook entry was missed: re-inspect `~/.claude/settings.json` and
`bash_gate.py` and repeat Step 2S.

---

## Report when done

State plainly: which install shape was found; exactly what was removed and what was intentionally
kept (reviewer scratch — and whether a shared custom scratch root was left for the operator to clear
by hand — state, briefs, the timeout key, any preflight hook); the backup location from Step 0; and
the fact that a restart is required before the change is live. If you stopped early, say at which step
and why — a truthful partial report is more useful than a confident summary of a teardown that did not
finish.
