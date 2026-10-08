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

## The one thing that trips up every local update

**How `claude plugin update` decides whether there is anything to do depends on the marketplace
source, and the two behave differently:**

- A **GitHub marketplace** source updates by **upstream commit**. `claude plugin marketplace
  update` fetches, then `claude plugin update` installs whatever the new commit resolves to. This
  is the normal end-user case and it just works.
- A **local Directory** source — a checkout you `marketplace add`ed by path, i.e. the developer
  case — updates by comparing the **`version` string in the manifest**, not the file contents and
  not the Git commit. **If the version string has not changed, `claude plugin update` prints
  `already at the latest version` and copies nothing, even though the files on disk have
  changed.** A day of committed work behind an unchanged `version` is invisible to it.

So a local update has a prerequisite the GitHub case does not: **the version must be bumped**, or
the installed copy stays stale with no error. Bumping is the intended fix (see
[Release procedure](CONTRIBUTING.md#release-procedure) and the checklist at the end of this file);
a forced reinstall at the same version is the fallback. Determine which source you have at step 1
and do not skip the version bump for a Directory source. To test commits **before** they are
released, do not bump; follow
[*Testing an unreleased build without a version bump*](#testing-an-unreleased-build-without-a-version-bump).

One more Directory-source subtlety worth knowing before you rely on the recorded metadata: an
install **copies the working tree** at update time (uncommitted edits included), but records the
current **`HEAD`** as `gitCommitSha`. So uncommitted local changes *are* installed, yet the
recorded sha will not reflect them. For a release you can trust, commit first, then update.

## Read this before running anything

An update writes outside the repository. The full list, before the first command:

| path | what an update does | reversible? |
|---|---|---|
| Claude Code's plugin cache (`~/.claude/plugins/cache/reviewer-agent/…/<version>/`) | a **new** version directory is copied in; the previous one remains until the host garbage-collects it | yes, roll back by reinstalling the prior version |
| `~/.claude/plugins/installed_plugins.json` | the `reviewer-agent` entry's `version`, `installPath`, `gitCommitSha` and `lastUpdated` are rewritten | yes, reinstall |
| `~/.claude/reviewer-agent/` (flags, risk receipts, backend selection, sessions, verdicts, logs) | **nothing** — an update does not touch state. Per-repo enablement and the accepted-risk receipt survive, so you do **not** re-accept or re-enable after an update | n/a |
| `~/.pi/agent/extensions/git-read-only/` | **nothing** — a plugin update does **not** refresh the Pi extension. If the extension changed and you use the `pi` backend, refresh it by hand (step 6) | yes, delete the directory |
| the **running** Claude Code session | **nothing takes effect until restart** — hooks are snapshotted per session, so the live session keeps running the old code (the gate still works, on the old version) until Claude Code is restarted | n/a |

**What this gates is a fail-closed commit gate.** An update never disables it. Throughout the
version-bump update the repository stays gated on the old code, and after the restart it is gated on
the new code. The one gap is the uninstall-then-reinstall fallback (step 4), which is briefly
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
`SessionStart` hooks launch Codex even when Pi is selected. Migrate it per the README's
[*Upgrading from the standalone Claude Code install*](README.md#upgrading-from-the-standalone-claude-code-install)
before continuing — and read the `bash_gate.py` caution there; do not simply delete it.

---

## Step 1 — identify what is installed and its source

```bash
claude plugin list                 # note the reviewer-agent@… version and that it is enabled
claude plugin marketplace list     # note the reviewer-agent marketplace's Source
```

**Check:** you can state (a) the installed version, and (b) whether the reviewer-agent marketplace
`Source:` is `GitHub (…)` or `Directory (/path/to/checkout)`. The rest of the procedure branches on
that source. `<PLUGIN_DIR>` below is that checkout path for a Directory source.

## Step 2 — verify the checkout before trusting it (Directory source)

Skip to step 3 for a GitHub source. For a Directory source you are the one publishing the new code,
so prove it first — do not ship a gate whose own tests fail:

```bash
cd <PLUGIN_DIR>
python3 -m pytest tests/ -q -p no:cacheprovider
python3 tools/check_split_layering.py
python3 tools/release_versions.py
```

**Check:** the suite passes (a small number of environment-dependent skips is normal);
`check_split_layering.py` prints `PASS [VERIFY]: acyclic, zero back-edges`; and
`release_versions.py` prints `PASS: every declared version normalizes to <version>`. The version it
names is what you are about to publish — confirm it is the **new** number, not the installed one. If
it still shows the installed number, bump it first (see the [checklist](#appendix--cutting-the-new-version-directory-source)).

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

**If instead it prints `already at the latest version`:** for a GitHub source, you are genuinely
current. For a **Directory source whose files you changed, this means the version string did not
change** — the update compared the string, saw no difference, and did nothing. Bump the version
(appendix) and repeat from step 3. If you cannot bump it, force a re-copy by reinstalling at the
same version:

```bash
claude plugin uninstall reviewer-agent
claude plugin install reviewer-agent@reviewer-agent
```

Uninstall-then-install preserves `~/.claude/reviewer-agent/` state, so the repo stays enabled and
the risk receipt survives — but the repository is briefly un-gated between the two commands, so do
it deliberately and prefer the version bump.

**Optional check — confirm the copy is actually current.** Compare a file you know changed against
the freshly installed copy (substitute the reported `<new>` version):

```bash
python3 -c "import json,os;e=json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['reviewer-agent@reviewer-agent'][0];print(e['version'], e['installPath'], e['gitCommitSha'])"
diff -q <PLUGIN_DIR>/stop_hook.py "$(python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/plugins/installed_plugins.json')))['plugins']['reviewer-agent@reviewer-agent'][0]['installPath'])")/stop_hook.py" && echo "IN SYNC"
```

**Check:** the version is the new one and `stop_hook.py` reports `IN SYNC`.

## Step 5 — restart Claude Code

`claude plugin update` prints `Restart to apply changes` for a reason: **hooks are snapshotted per
session.** The session you are in keeps the old hook code until Claude Code restarts. Nothing you
did in steps 3–4 reaches a running session.

**Check (after restart):** `claude plugin list` shows the new version. The accepted-risk receipt and
per-repo enablement are unchanged, because state was never touched.

If `stopContinue` and `stopContinueWatchdog` are active for a repository, the watchdog recovery cron
is re-armed automatically and you do not re-arm it by hand — but **not by `SessionStart`, and not at
the restart itself.** The restart destroys the cron (it lives in the host session's memory, not on
disk), and `SessionStart` emits an arm instruction that the host does **not** deliver on a resumed
session, so nothing has re-armed it by the time the new session opens.

What recovers it is tick liveness, and the timing is **bounded rather than immediate**. The
`tick_seen` marker is on disk and survives the restart that killed the cron, so a prompt only
triggers the re-arm once that marker is older than two watchdog intervals (14 minutes at the default
7). Restart shortly after a tick and the marker still looks fresh: the cron is genuinely gone, yet
the next prompt will *not* re-arm it. So:

- an empty `CronList` immediately after a restart is expected, not a fault;
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

Codex, like a Directory-source Claude Code install, resolves an installed local plugin **by version
string** and will serve its cache for an unchanged version. The `+codex.<timestamp>` build metadata
exists precisely to defeat that: refreshing it is what makes Codex notice a reinstall.

```bash
git -C <PLUGIN_DIR> pull                         # if updating a tracked checkout
python -m pip install -r <PLUGIN_DIR>/requirements-dev.txt
(cd <PLUGIN_DIR>/plugins/reviewer-agent-codex && python -m pytest -p no:cacheprovider)
python3 <PLUGIN_DIR>/tools/release_cachebuster.py  # refresh +codex.<UTC>; never edit it by hand
python3 <PLUGIN_DIR>/plugins/reviewer-agent-codex/scripts/install_local.py
```

After installation, run `codex` in a terminal and use `/hooks` in that interactive CLI to trust
the plugin hooks again.
Exit the CLI, fully quit and restart the Codex GUI, and start a new GUI chat. `/hooks` is not a
Codex GUI command. State under `~/.codex/reviewer-agent-codex/` survives,
so features stay enabled per repository — no re-acceptance.

The commands above install test dependencies and run the native plugin suite in an explicit
package-directory subshell before installation. Use an available Python 3.9+ interpreter
(and a virtual environment if needed) for `python`.
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
python3 tools/release_matrix.py --all --report /tmp/matrix.md   # real reviewer calls (spends quota)
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

Alongside the matrix, the always-run gates for any release are the two unit suites plus the
structural and parity checks — see [`CONTRIBUTING.md`](CONTRIBUTING.md#running-the-suites).

## Testing an unreleased build without a version bump

A version bump on the public repository tells every user to upgrade, so it waits until testing is
complete. To try unreleased commits first, install an **export** of them at the version label
they already carry, test, then return to the GitHub marketplace.

1. **Export to a permanent directory**, named for the commit so a later export can sit beside it.
   The export copies the **working tree**, so run it from a clean checkout of that commit (a
   `git worktree add <dir> <sha>` is the simplest), and confirm `git status --short` prints
   nothing first. Otherwise you test edits the named commit does not contain.

   ```bash
   git -C <clean-checkout> status --short          # must print nothing
   python3 <clean-checkout>/tools/release_export.py --target ~/reviewer-agent-builds/<short-sha>
   ```

   **Never use a scratchpad or anything under `/tmp`.** A Directory marketplace runs the plugin's
   hooks from that directory, not from a cached copy. macOS deletes `/tmp` files unused for 3 days.
   On 2026-10-07 that happened to a test build: every hook then exited 127, which Claude Code
   treats as non-blocking, so stop supervision stopped and **the commit gate failed open**. The
   export refuses a non-empty target, so a refresh is a new directory, not an overwrite.

2. **Point the marketplace at the export and install from it:**

   ```bash
   claude plugin marketplace remove reviewer-agent
   claude plugin marketplace add ~/reviewer-agent-builds/<short-sha>
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

Lead with the **installed version before and after**, the **marketplace source** (GitHub vs
Directory), and — for a Directory source — **whether you had to bump the version** to make the
update take. Then state plainly: whether the restart happened, whether state survived (it should
have, so per-repo enablement needs no redo), whether the Pi extension needed refreshing, which
matrix scenarios you ran and their result, and which human-only scenarios remain outstanding. If
you stopped early, say at which step and why.
