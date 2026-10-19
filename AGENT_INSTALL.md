# Installing reviewer-agent unattended


> **Informational only — not a warranty, and not to be relied upon.** This document describes intent, not guaranteed behaviour. It may be incomplete, inaccurate, or out of date, and the software may behave differently from anything written here. Nothing in it is a representation, guarantee, or commitment of any kind. Use of this software is entirely at your own risk: see [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

A procedure for an **agent** that has been pointed at this repository and told to install it. Every
step has a command and a check that proves it worked. Do not proceed past a failed check.

This guide installs the **Claude Code** plugin at the repository root. For agent-assisted native
Codex installation or reinstallation, start at [`INSTALL_WITH_AGENT.md`](INSTALL_WITH_AGENT.md).
It includes the settings and operator checkpoints and links to the native package's install guide;
its installer also performs Codex-specific direct-tool configuration.

If you are a human, [`README.md`](README.md) is shorter.

---

## Read this before running anything

**This installation writes outside the repository.** You are acting on someone's machine, so the
full list is here, before the first command rather than buried in a step:

| path | what happens | reversible? |
|---|---|---|
| Claude Code's plugin directory | the plugin is installed | yes, `/plugin uninstall reviewer-agent` (full teardown: [`AGENT_UNINSTALL.md`](AGENT_UNINSTALL.md)) |
| `~/.claude/reviewer-agent/` | created for flags, session ids, logs, verdicts | yes, delete the directory |
| system temp base `reviewer-agent-scratch/` | **codex backend only:** per-reviewer-session scratch for the streamed codex audit (`REVIEWER_SCRATCH_ROOT` relocates it) | yes, auto-reclaimed at rotation / by a lease sweep; safe to delete |
| `~/.pi/agent/extensions/git-read-only/` | **only if** the backend is `pi` | yes, delete the directory |
| `<PLUGIN_DIR>/pi-extensions/git-read-only/node_modules/` | **only if** the backend is `pi` (~131 MB, gitignored) | yes, delete the directory |
| `<TARGET_REPO>/reviewerAgent.md` | created at step 5 **if absent** | yes, delete the file |

**Nothing here overwrites an existing file, and two steps need an explicit check to keep that
true.** Step 5 writes a brief and the optional pi section copies a directory; both are told to
refuse rather than clobber, because a repository's existing `reviewerAgent.md` is hand-written
content nobody wants replaced by a template, and an existing pi extension may be a newer copy than
this one.

## Two paths, and they are not the same directory

Later steps move between the plugin checkout and the repository being gated. Fix both up front and
refer to them by name; a command run from the wrong one fails confusingly or writes to the wrong
tree:

- **`<PLUGIN_DIR>`** — a permanent user-owned checkout containing `AGENT_INSTALL.md` and `tests/`,
  outside the protected repository.
- **`<TARGET_REPO>`** — the repository in which developer-agent commit attempts will be gated.
  Resolve both paths and require distinct directories; do not put the plugin checkout inside
  `<TARGET_REPO>`.

Shell commands below carry an explicit `cd`, so no step depends on where the previous one left you.

**Slash commands are different, and this is the trap.** `/reviewerAgent`, `/stopContinue` and the
rest act on the directory **the Claude Code session is running in**. A `cd` inside a Bash step
changes a subprocess, not the session, so it cannot move them. If you run the accepting
`/reviewerAgent on acceptRisk` invocation from a session opened in `<PLUGIN_DIR>`, you enable the
gate on the plugin checkout and the target repository stays ungated — with no error, because that
is a legitimate thing to ask for.

So: **steps 4 to 7 must be run from a Claude Code session whose working directory is
`<TARGET_REPO>`.** If your session is in `<PLUGIN_DIR>`, finish step 3 there, then open a
session in `<TARGET_REPO>` and continue from step 4. Do not try to work around this from the shell.

There is no status subcommand to check this with beforehand. What you get instead is the path in
its own success line:

```
reviewerAgent: ON for <root> - the reviewer is briefed on first use, ...
```

**Read that `<root>` at step 6 and compare it to `<TARGET_REPO>`.** If it names a different
repository you have gated the wrong one: run `/reviewerAgent off` immediately, which prints
`reviewerAgent: OFF for <root>` and takes effect at once, then start a session in the right place
and repeat step 6.

**What this installs is a fail-closed commit gate.** Once enabled in a repository, a `git commit`
there is blocked unless a reviewer approves it, and a reviewer error also blocks. That is the
intended behaviour, not a malfunction. If the person you are acting for has not asked for that,
stop and confirm before step 6.

**Stop and report, do not improvise, if:** no backend the operator is willing to use is present (step 2 records the
operator's backend choice and its audit trade-off); the chosen backend is unauthenticated; the test
suite fails; or any check below does not produce the stated result. A partially installed fail-closed gate can block every commit in every repository, so a
half-finished install is worse than none.

**Stop if a standalone predecessor is still registered.** Before installing the plugin, inspect
the user hook commands without printing unrelated settings:

```bash
python3 -c "import json,os;p=os.path.expanduser('~/.claude/settings.json');d=json.load(open(p)) if os.path.exists(p) else {};print('\n'.join(f'{event}: {h.get(\"command\", \"\")}' for event,groups in (d.get('hooks') or {}).items() for group in groups for h in (group.get('hooks') or []) if 'revieweragent' in h.get('command','').lower() or h.get('command','').endswith('/bash_gate.py')))"
```

If it prints commands under `~/.claude/reviewerAgent/` or `~/.claude/hooks/bash_gate.py`, stop and
report that the standalone hooks must be migrated before plugin installation. Do not simply delete
`bash_gate.py`: some older installs use it to chain a separate preflight hook that may still be
wanted. Follow the uninstall guide's [backup](AGENT_UNINSTALL.md#step-0--back-up-first) and
[legacy-hook removal](AGENT_UNINSTALL.md#step-2s--tear-down-the-legacy-standalone-the-delicate-one)
steps, preserving retained state and unrelated hooks; optional data purges are not part of this
migration. Running both copies causes duplicate audits and older `SessionStart` hooks launch
Codex even when Pi is selected.

---

## Step 1 — confirm the platform

```
uname -s                 # expect: Darwin or Linux
command -v python3       # expect: an executable path
python3 -c "import sys;print(sys.version.split()[0]);sys.exit(sys.version_info < (3, 9))" && echo "SUPPORTED"
if test -n "${WSL_DISTRO_NAME:-}" || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  grep -qi 'WSL2' /proc/sys/kernel/osrelease || { echo "ERROR: WSL2 not detected"; exit 1; }
fi
```

**Check:** the platform, interpreter, version (`SUPPORTED`), and conditional WSL2 checks all
succeed. The hooks run the first `python3` on the PATH Claude Code is started with, so an
activated venv or conda environment decides it; this check predicts it only if Claude Code will be
started with this shell's PATH. Step 6 repeats it in the restarted target session. macOS has
daily use. Native Ubuntu installations produced issue reports; acceptance of the current fixes
on a clean Linux host remains pending. Windows through WSL2 is an **untested beta** that is expected
to work; tell the operator that before proceeding and continue only when the original request
includes that beta platform or the operator confirms it.
For WSL2, the executable guard above must pass. The operator must also run
`wsl --list --verbose` in PowerShell and verify that the selected distribution's `VERSION` column
shows `2`; if it shows `1`, stop until they have backed up important distribution data and run
`wsl --set-version <distribution-name> 2`. Both `<PLUGIN_DIR>` and `<TARGET_REPO>` must be in the
Linux filesystem (for example under `~/code`), not `/mnt/c`.

Stop on WSL1 or native Windows. WSL1 is outside this guide's common beta path: Claude Code can run
there without sandboxing, but current Codex cannot, and this project has not tested the Claude plus
Pi combination on WSL1. Run this entire procedure, Claude Code, the selected reviewer backend, and
the protected repository inside WSL2. See *Platform support* in the README for setup directions and
the bug-report checklist. Do not edit `hooks/hooks.json` to work around a platform failure; that
file is plugin-managed and an update will silently revert the change.

If the initial working directory is not inside a Git repository, stop and ask the operator which
repository to protect; that answer is `<TARGET_REPO>`. Then check that Git has a complete author
identity for it (a name AND an email):

```
git -C <TARGET_REPO> var GIT_AUTHOR_IDENT
```

If that fails, ask the operator to set `user.name` and `user.email`; otherwise the first commit
fails for a reason that has nothing to do with the gate.

## Step 2 — detect the backends, then ASK which one to use

Resolve the backend prerequisite here. Defer feature settings confirmation to step 6, after
installation and host restart. Then follow the shared
[settings walkthrough](INSTALL_WITH_AGENT.md#2-resolve-choices-once), with a settings screen and
an immediate enablement screen for each feature before moving to the next feature. Do not use a
premature pre-install settings answer to skip that checkpoint.

There are three, `codex`, `pi` and `claude`. **None is a requirement and you do not get to pick
for them.** Detect what is present, then put the choice to the operator — do not infer it from
what happens to be installed, and do not let the resolution default decide silently.

```
command -v codex || ls ~/.local/bin/codex
command -v pi
command -v claude
```

`claude` is present on every Claude Code host by definition, so its presence is not evidence that
anyone wants it as the reviewer. **Never select it silently.** State that the child uses the
operator's provider account and that usage, quota, billing, availability and account consequences
are controlled by Anthropic and the terms applicable to that account.

Now branch on what you found. In every case the operator answers before you continue:

- **More than one available** — **ask which one to use.** State the relevant trade-off: every
  backend handles commit audits, the `AskUserQuestion` reroute, and stop supervision; the current *codex*
  configuration can run tests during an audit; the current *pi* and *claude* configurations omit
  shell tools; and *claude* uses the operator's provider account. Do
  not default to codex because it is listed first here.
- **Only codex or only pi present** — **ask them to confirm that one**, naming it, and name
  `claude` alongside it as the no-extra-install alternative with its quota cost. This is a
  confirmation, not a formality: they may have meant to install the other. If only `pi` is
  present, do not install Codex as an additional dependency; selecting Pi routes all three Claude
  Code features through Pi.
- **Neither codex nor pi present** — **do not install either.** Offer `claude`, with its quota
  cost stated, and let them choose it or stop to install a backend themselves. Do not install
  `codex` merely to satisfy this step if they asked for `pi`. The three are alternatives, not
  layers.

**Then record the answer, because not recording it is itself a choice.** With nothing configured,
`reviewer_backend()` resolves to `codex`, so an operator who chose `pi` or `claude` and got no
write silently gets codex instead.

**Write it through the library, not with `echo` into a fixed path.** `REVIEWER_STATE_DIR` relocates
the whole state directory, so a hard-coded `~/.claude/reviewer-agent/...` can write a file nothing
reads. `set_default_backend()` resolves the configured location for you. The same script then
reports the two sources that OUTRANK what it just wrote, since either would silently mask the
operator's choice:

Substitute the operator's actual answer for `<BACKEND>` below — `codex`, `pi` or `claude`. Do
not leave a literal, and do not assume one:

```
python3 - <BACKEND> <<'PY'
import os, sys
sys.path.insert(0, "<PLUGIN_DIR>")
import reviewer_lib as rl

choice, target = sys.argv[1], "<TARGET_REPO>"
if not rl.is_valid_backend(choice):
    raise SystemExit("invalid backend: " + choice)
rl.set_default_backend(choice)                 # honours REVIEWER_STATE_DIR
print("wrote:", rl.backend_default_file())

def masking(label, value):
    # A source masks the choice only when it is VALID and DIFFERENT. An invalid token is
    # skipped by the resolver, and one equal to the choice overrides nothing -- reporting
    # either would fail this check on a correctly configured machine.
    value = (value or "").strip()
    if not value or not rl.is_valid_backend(value) or value == choice:
        return None
    return label + "=" + value

masks = []
m = masking("REVIEWER_BACKEND", os.environ.get("REVIEWER_BACKEND"))
if m:
    masks.append(m)
proj = rl.project_backend_file(target)
if proj.exists():
    try:
        m = masking("per-project " + str(proj), proj.read_text(encoding="utf-8"))
    except OSError:
        m = None
    if m:
        masks.append(m)

print("MASKED BY:", "; ".join(masks) if masks else "nothing")
print("resolved:", rl.reviewer_backend(target))
PY
```

**Check:** `MASKED BY: nothing`, and `resolved:` equal to the operator's choice. If a mask is
reported, clear it or set that higher-precedence source instead — do not leave a recorded choice the
resolver ignores. For a single repository, `rl.set_project_backend(target, choice)` is the right
writer instead of the global one.

**If they chose `pi`, persist its absolute path too — this is not optional on an `nvm` install.**
Hooks resolve `pi` from their OWN environment, which does not inherit your interactive shell's
`PATH`. Measured with a hook-like `PATH=/usr/bin:/bin`, `pi_bin()` falls back to the bare string
`"pi"`, the exec fails, and because the gate is fail-closed **every commit in the repo is blocked**.
Persisting `piBin` returns the absolute path under the same stripped environment:

```
python3 - <<'PY'
import json, os, shutil
state = os.path.expanduser(os.environ.get("REVIEWER_STATE_DIR", "~/.claude/reviewer-agent"))
os.makedirs(state, exist_ok=True)
cfg = os.path.join(state, "config.json")
data = json.load(open(cfg)) if os.path.exists(cfg) else {}
found = shutil.which("pi")
if not found:
    raise SystemExit("pi not on PATH - cannot persist piBin")
data["piBin"] = found
json.dump(data, open(cfg, "w"), indent=2)
print("piBin =", found)
PY
```

**Check — verify what the HOOKS will resolve, not what your shell resolves**, and verify the
*resolved* backend rather than the file you just wrote (two sources outrank it — the environment
variable and the per-project override — and `REVIEWER_STATE_DIR` relocates it entirely):

```
python3 -c "import sys; sys.path.insert(0, '<PLUGIN_DIR>'); import reviewer_lib as rl; print('backend:', rl.reviewer_backend('<TARGET_REPO>'), '| pi_bin:', rl.pi_bin())"
```

**Check:** `backend:` is what the operator chose. If they chose `pi`, `pi_bin:` must be an absolute
path — a bare `pi` means the persist step did not take, and commits will be blocked.

If they chose `pi`, the **pi backend** section below is mandatory, not optional — without the
`git_read` extension pi cannot read the repository at all, and the gate is fail-closed, so every
commit in the repo would be blocked. Complete it before step 6.

**If they chose `codex`, check what the hooks will resolve, and persist its path when they
would not find it.** Hooks resolve codex from `REVIEWER_CODEX_BIN`, then `codexBin` in the
active config file, then `~/.local/bin/codex`, then `codex` on their own `PATH`, which is often
just `/usr/bin:/bin`. An npm-global install elsewhere (for example under `nvm`) is then invisible
to them and every commit is blocked. An npm install of codex is also a Node script
(`#!/usr/bin/env node`), which then needs `node` on that `PATH` as well; a native codex binary
does not. This asks the plugin's own resolver, under a hook-like `PATH`, and writes `codexBin`
to the config file that resolver reads only when it finds nothing:

```
python3 - <<'PY'
import json, os, shutil, sys
sys.path.insert(0, "<PLUGIN_DIR>")
import reviewer_lib as rl

shell_path = os.environ.get("PATH", "")
hook_path = "/usr/bin:/bin"


def hook_resolves():
    # The resolver the hooks use (REVIEWER_CODEX_BIN, codexBin in the active config file,
    # ~/.local/bin/codex, then PATH), evaluated under a hook-like PATH.
    os.environ["PATH"] = hook_path
    try:
        found = rl.codex_bin()
    finally:
        os.environ["PATH"] = shell_path
    return found if os.path.isfile(found) else None


seen = hook_resolves()
print("hooks resolve codex:", seen or "NOTHING")
target = seen or shutil.which("codex")
if not target:
    raise SystemExit("codex is not on this shell's PATH either - ask the operator where it is")
with open(os.path.realpath(target), "rb") as fh:
    first = fh.readline(200)
if first.startswith(b"#!") and b"node" in first:
    node = shutil.which("node", path=hook_path)
    print("node script; node on the minimal hook PATH:", node or "NOTHING - ask the operator")
if not seen:
    if os.environ.get("REVIEWER_CODEX_BIN"):
        raise SystemExit("REVIEWER_CODEX_BIN is set but names no file - fix or unset it")
    cfg = rl.config_file()                        # honours REVIEWER_CONFIG_FILE / state dir
    cfg.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(cfg.read_text()) if cfg.exists() else {}
    data["codexBin"] = os.path.abspath(target)    # the stable link, not a versioned release dir
    cfg.write_text(json.dumps(data, indent=2) + "\n")
    print("wrote codexBin =", data["codexBin"], "to", cfg)
    print("hooks resolve codex now:", hook_resolves() or "NOTHING")
PY
```

**Check:** the last `hooks resolve codex` line names an existing file. `/usr/bin:/bin` is the
worst case, not necessarily the real hook `PATH`: hooks inherit the `PATH` Claude Code itself was
started with, which this shell cannot see. So if the script printed `node on the minimal hook
PATH: NOTHING`, **stop and ask the operator** whether Claude Code is launched with `node` on its
`PATH` (for example from a shell whose profile adds it). Continue only if they confirm. Otherwise
this codex cannot start from a hook and the fail-closed gate would block every commit; the usual
fix is a system-wide Node install.

## Step 3 — verify the checkout before trusting it

Select an available Python 3.9+ interpreter and create a new test environment outside the
checkout, as described in `INSTALL_WITH_AGENT.md`. No named conda environment is required.
Install test dependencies before invoking pytest; the runtime remains standard-library-only.

```bash
python3 -m venv <TEST_VENV>
<TEST_VENV>/bin/python -m pip install -r <PLUGIN_DIR>/requirements-dev.txt
cd <PLUGIN_DIR> && <TEST_VENV>/bin/python -m pytest -m smoke -q -p no:cacheprovider -rs
cd <PLUGIN_DIR> && <TEST_VENV>/bin/python tools/check_split_layering.py
```

Reuse an environment prepared by the shared guide rather than creating it twice. Keep the
plugin checkout in a permanent directory; only the test environment may be temporary.

`-m smoke` runs the install-time subset: imports, distribution shape, host versions, the hook
launcher and a real-git gate round trip, in under a minute. The full suite (about 1,500 tests,
5–12 minutes) is for `CONTRIBUTING.md`; if the shared guide already ran it and it passed, the smoke
run adds nothing. If you run the full suite, use background execution and wait for its exit
status rather than a foreground command timeout.

**Check:** every test passes; the structural check prints
`PASS [VERIFY]: acyclic, zero back-edges`. Compare the `-rs` output against these expected skips:

- smoke run: none, except when running as root, where
  `tests/test_commit_intent.py`: `root can enter any directory` skips.
- full suite, if run: the root-only skip above, plus exactly these three:
  - `tests/test_split_shared_state.py`: `reviewer_state._MIGRATED_CONTROL_STEMS has no reset writer`
  - `tests/test_split_shared_state.py`: `reviewer_manifest._TOPLEVEL_CACHE has no reset writer`
  - `tests/test_reviewer_lib.py`: `REVIEWER_PROMPT_SPEC.md is maintainer-local and absent here`

Any other skip is unexplained: report it rather than proceeding.

If the suite fails, **stop**. You are about to install a gate that blocks commits; installing one
whose own tests fail is not a recoverable position.

## Step 4 — install the plugin

An installing agent should use Claude Code's non-interactive plugin CLI:

```bash
claude plugin marketplace add <PLUGIN_DIR>
claude plugin install reviewer-agent@reviewer-agent
claude plugin list
```

An operator performing the steps interactively may use `/plugin marketplace add <PLUGIN_DIR>` and
`/plugin install reviewer-agent@reviewer-agent` instead. Use `<PLUGIN_DIR>` when installing from a
checkout; use the published `rladr-org/reviewer-agent` marketplace when installing from GitHub.

**Check:** `claude plugin list` shows `reviewer-agent@reviewer-agent`. After the new-session boundary,
the commands `/reviewerAgent`, `/reviewerDirective`, `/reviewerModel`, `/reviewerSession` and
`/stopContinue` are offered.

Hooks are snapshotted per session at start. An installing agent may finish steps 5 and 5b, but it must not
accept risk or enable either feature for the operator. The human must restart Claude Code before
step 6. A session that was already running keeps the old hook snapshot, so the gate can appear
installed and do nothing.

## Step 5 — write the brief

**This comes before enabling, and not as a matter of taste: `/reviewerAgent on` refuses a
repository that has no brief.** It exits with `reviewerAgent: ERROR - brief not found at <path>`
and changes nothing, so step 6 cannot succeed until this one has.

`reviewerAgent.md` at the repository root is the reviewer's standing brief. Its contents are sent
to the reviewer verbatim, once per Claude session (at session start, or on the first gated
action), and establish the reviewer session that every later commit resumes. It is read from the
canonical (main worktree) root, so one brief covers every linked worktree. The generic built-in
brief is only a review-time fallback for a brief that has become empty or unreadable, so a corrupt
brief cannot block every commit. Because the whole file is sent, it must contain only instructions
for the reviewer — no setup notes for humans.

**Refuse to overwrite an existing brief.** `cp` would silently replace hand-written content with a
template:

```
test -e <TARGET_REPO>/reviewerAgent.md && echo "EXISTS - leave it alone, do not overwrite" || cp <PLUGIN_DIR>/reviewerAgent.md.template <TARGET_REPO>/reviewerAgent.md
```

If it already exists, **report that** and leave the file untouched — it is very likely better than
the template — then continue to step 5b.

If you created it, **edit it for that repository**: what the reviewer must enforce, the conventions
it should know, the defect classes this codebase actually produces, and its test command. Replace
the template's `Project-specific guidance (edit me)` paragraph entirely. Do not leave the template
unedited — a generic brief produces generic reviews, and this step is what determines whether the
tool is worth having. `/reviewerAgent on` warns if the placeholder is still there.

**Check:** `<TARGET_REPO>/reviewerAgent.md` exists and describes that repository specifically, and
`grep -c "edit me" <TARGET_REPO>/reviewerAgent.md` prints `0`.

**Optional for data-heavy repositories:** if the repository routinely commits large volumes
of auto-generated data (dataset rows, generated manifests, captured HTML), consider adding a
`<TARGET_REPO>/reviewerAgent-ignore.md` at the same location. It names paths the reviewer
should skim instead of judging record-by-record — the reviewer still audits schema and
placement, but per-record content correctness is left to the pipeline that produced them.
This reduces prompt tokens on codex retries and can lower the likelihood of timeout
failures on large data commits — the initial audit still runs on the full staged patch,
and codex may fetch a matched path on demand, so it is a mitigation rather than a guarantee. See the "Skimmed paths" section of the README for the fenced-block format. The file
is optional; a missing file matches today's behavior (no exclusions).

## Step 5b — offer the settings rules (agent, on approval)

These two optional blocks go in the operator's `~/.claude/settings.json`. Show them to the
operator, explain what each allows, and write them only if the operator approves. They are
not needed when auto mode is off. If your own write to `settings.json` is refused (auto mode can
block an agent editing its host's settings), give the operator the JSON to add themselves.

**One permission rule for the manual commands.** The six manual commands (`/reviewerAgent`,
`/reviewerHistory`, `/reviewerModel`, `/reviewerSession`, `/stopContinue`, `/stopContinueWatchdog`)
run a `!` shell pre-exec through a single dispatcher, `reviewer_cmd.py`. In Claude Code `auto` mode a
pre-exec with no matching allow-rule is blocked before it runs, so add ONE scoped rule to
`~/.claude/settings.json`. Permission-rule paths are literal (no `${CLAUDE_PLUGIN_ROOT}` expansion),
and the installed path is version-stamped —
`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/reviewer_cmd.py` — so a rule that pins the
version goes STALE on the next `claude plugin update`. Wildcard the version segment with `*` so the
rule survives updates: run `claude plugin list` for your install path, then keep every segment
literal EXCEPT the version, which becomes `*`. End the rule with a space and `*` (` *)`), not the
legacy `:*` suffix: Claude Code recognizes `:*` only at the end of a pattern and matches everything
before it literally, so `.../*/reviewer_cmd.py":*` compares the `*` as a literal character, never
matches, and draws a startup warning that it "mixes * with the trailing :* prefix syntax".

A Bash-rule `*` matches ANY text, including `/` and `..` — it does not stop at one path segment — so
the allow rule alone would also approve `.../reviewer-agent/../../../tmp/x/reviewer_cmd.py`, an
arbitrary script that merely shares the name. Pair it with a deny rule (deny rules take precedence)
that refuses any `..` after the plugin-cache prefix:

```json
{ "permissions": {
  "allow": ["Bash(python3 \"/ABSOLUTE/PATH/reviewer-agent/*/reviewer_cmd.py\" *)"],
  "deny": ["Bash(python3 \"/ABSOLUTE/PATH/reviewer-agent/*..*)"]
} }
```

The deny rule also refuses a normal invocation whose arguments contain `..`; none of the reviewer
commands take such an argument.

`/reviewerDirective` needs no rule — it is handled by a `UserPromptSubmit` hook, not a shell.

**Auto mode may block `on` even with the allow-rule above.** The permission rule lets the
non-state-changing invocations run; on some Claude Code versions a state-CHANGING invocation (`on`)
is independently flagged by auto mode's classifier and refused even with the rule. Either turn auto mode off for the single
`on acceptRisk` invocation, or add an `autoMode.allow` classifier exception — read from
`~/.claude/settings.json` (and managed settings) only, keep `"$defaults"`, and it survives updates
because it names the dispatcher rather than a version:

```json
{ "autoMode": { "allow": [
  "$defaults",
  "Running the reviewer-agent dispatcher reviewer_cmd.py (under ~/.claude/plugins/cache/reviewer-agent/) with any subcommand and arguments, including its on/off/acceptRisk toggles: it only reads or flips this tool's own per-project flag files under ~/.claude/reviewer-agent/state and prints status; it never edits code, runs git, or accesses the network."
] } }
```

## Step 6 — settings confirmation, then human acceptance and enablement

Follow the interactive conversational format in the
[shared settings walkthrough](INSTALL_WITH_AGENT.md#2-resolve-choices-once): explain the system
and settings, using the required settings-table columns, before asking one keep/change question
and yielding for the answer. Honor the
operator's requested format; do not use question cards when they have rejected them. Keep settings
confirmation separate from enablement and the operator's exact acceptance command.

First verify installation and the restart in `<TARGET_REPO>` required by step 4. Then check the
hooks' Python from this restarted session, whose shell has the PATH the hooks use:

```bash
command -v python3
python3 -c "import sys;print(sys.version.split()[0]);sys.exit(sys.version_info < (3, 9))" && echo "SUPPORTED"
```

**Check:** it prints `SUPPORTED`. Report the path and version, and name the environment if one is
active (`CONDA_DEFAULT_ENV`, `VIRTUAL_ENV`). If it is older than 3.9, stop and report: the
operator must start Claude Code with a supported `python3` first on the PATH. Tell the operator
that starting Claude Code later under another environment, or none, changes the hooks' Python.

Complete the
shared [settings walkthrough](INSTALL_WITH_AGENT.md#2-resolve-choices-once) in order: commit-review
settings and enablement, Stop settings and enablement, then rotation/history. The acceptance
instructions below are a reference for each feature's enablement screen, not a reason to skip its
settings confirmation. If settings were asked prematurely, follow the shared recovery rule and
ask again now using the loaded effective values.

Tell the operator explicitly that installation and host hook permissions do not accept the risk
or enable these features. Report the target and current feature/receipt state, then give the exact
commands below; do not leave the operator to discover the acceptance requirement. A conversational
“yes” to enablement only confirms the preference. Display the short risk notice, disclaimer
link and applicable command, then wait for the operator to type the command at the prompt.
Verify its receipt/feature state as described in the
[shared checkpoint](INSTALL_WITH_AGENT.md#5-human-only-checkpoint-and-final-report).

Risk acceptance is operator-only. Point the operator to [`DISCLAIMER.md`](DISCLAIMER.md) on the
applicable enablement screen. If the restart in `<TARGET_REPO>` has not happened, finish that
prerequisite before presenting settings or enablement. The agent must not run either source toggle
or add `acceptRisk` on the operator's behalf.

**From the restarted Claude Code session whose working directory is `<TARGET_REPO>`** — see *Two
paths* above — the operator first invokes:

```
/reviewerAgent on
```

**If Claude Code's auto mode is on, the toggle may be blocked on some Claude Code versions**
with a *"Permission for this action was denied by the Claude Code auto mode classifier"* error
before the toggle script ever runs — the classifier reads the `on` argument as state-changing.
**Turn auto mode off** for the enablement invocation (in `/config`, or set `permissions.defaultMode` to `default` in
`~/.claude/settings.json`), invoke `/reviewerAgent on` (then `on acceptRisk`), and re-enable auto
mode afterward if you want it back, or use the `autoMode.allow` exception from step 5b. The same
applies to `/stopContinue on [acceptRisk]` and `/stopContinueWatchdog on`.

Without auto mode blocking it, the plain `on` invocation must refuse without enabling and display:

```text
Before enabling reviewerAgent you must accept the risk as described in DISCLAIMER.md by invoking:
/reviewerAgent on acceptRisk
```

After reading the disclaimer, the operator may invoke that exact command. If the operator selected
stop supervision, `/stopContinue on` may then be invoked normally because one current receipt is
shared by both features. If stopContinue is the first feature enabled instead, its plain `on`
invocation requires `/stopContinue on acceptRisk`.

**Check first — the path in the output.** `/reviewerAgent on acceptRisk` replies
`reviewerAgent: ON for <root> ...`. If `<root>` is not `<TARGET_REPO>`, the session is in the wrong
directory: `/reviewerAgent off`, then re-run from a session in `<TARGET_REPO>`. Do not continue
until the reported root is correct — everything after this point would be verifying the wrong
repository.

If instead it prints `reviewerAgent: ERROR - brief not found at <path>`, nothing was enabled: either
step 5 did not run, or the session is in a directory other than the one you wrote the brief in.
Compare the path it names against step 5's file before doing anything else.

## Step 7 — prove the gate actually gates

Do not declare success on configuration alone. Make a trivial commit in the target repository and
confirm a reviewer verdict appears — an approval or a rejection both prove the hook fired.

For an agent-assisted installation, do not manufacture a commit in the operator's repository. The
agent should finish installation before the human-only restart and acceptance step, report that
enablement and live gate verification are pending, and identify the first real commit after
enablement as the end-to-end check. If the operator explicitly requests an immediate proof, resume
after restart and agree on a harmless test commit before changing Git history.

**Check:** the commit is either audited and allowed, or blocked with reasons. If it completes with
no reviewer output at all, the hook did not run: return to step 4 and restart the session.

If it is blocked and you disagree with the finding, that is the tool working. Fix the finding or
disable with `/reviewerAgent off`.

---

## The `pi` backend — required if the operator chose `pi` at step 2

**Skip this entirely if they chose `codex` or `claude`.** If they chose `pi`, none of this is
optional: the reviewer withholds bash from Pi, so commit audits read the repository solely through
this extension. A missing or broken extension blocks every commit in the repository behind the
fail-closed gate.

Pi handles commit audits, the `AskUserQuestion` reroute, and stop supervision with whatever model Pi is
configured for. It never invokes Codex as a fallback. See *Claude Code backends* in the README.

```
cd <PLUGIN_DIR>/pi-extensions/git-read-only
node -e 'const [a,b]=process.versions.node.split(".").map(Number);
if (a<22 || (a===22 && b<19)) { console.error("Node 22.19+ required, have "+process.versions.node); process.exit(1) }
console.log("node ok "+process.versions.node)'
npm ci
node test/run.mjs                       # expect: 55 passed, 0 failed
PI_GIT_READ_LIVE=1 node test/live.mjs   # expect: 8 live tests passed
```

**Check:** the version guard EXITS NON-ZERO on an old Node rather than printing a number for you to
eyeball. `node --version` alone is not a check.

**Check:** both test runs pass. The env var on the second is **required** — without it the file
prints `skipped` and still exits 0, so a bare run proves nothing.

Then install the extension where pi discovers it, **refusing to replace an existing one** — it may
be a newer copy than this. Create the parent directory first: on a fresh pi install it does not
exist, and `cp -R` into a missing directory fails (or, worse, creates a *file* named
`extensions`):

```
mkdir -p ~/.pi/agent/extensions
test -e ~/.pi/agent/extensions/git-read-only \
  && echo "EXISTS - stop and report; do not replace it" \
  || cp -R <PLUGIN_DIR>/pi-extensions/git-read-only ~/.pi/agent/extensions/
```

If it exists, stop and report the collision rather than merging or replacing. Note that the copy
carries `node_modules/` if you ran `npm ci` above, which is intended: the installed extension needs
its dependencies.

Note that `pi list` will **not** show it — that command lists settings-registered packages, and
this is discovered from the extensions directory. Its absence there is expected.

Confirm the selection you recorded at step 2 is still in place — everything above is inert without
it, because an unset backend resolves to `codex`:

```
cat ~/.claude/reviewer-agent/state/backend.default 2>/dev/null || echo "(unset -> codex)"
```

**Check:** it prints `pi`. If it prints `(unset -> codex)` you skipped the step-2 write; go back and
record the operator's choice. The full precedence, highest first: `REVIEWER_BACKEND` in the
environment, `state/<project>.backend` for one repository, `state/backend.default` for all of them,
`"backend"` in `config.json`, then `codex`. An invalid token is skipped rather than honoured, so a
typo falls through to `codex` silently.

**Check:** make a trivial commit as in step 7 and confirm a verdict appears.

Set `REVIEWER_PI_MODEL` if the operator wants a specific model; unset means pi's own default. It
must be a single plain token — a value with spaces is skipped and you get the default with no
warning.

---

## Report when done

Lead with **which backend the operator chose, whether you recorded it, and what the read-back
printed** — that is the one decision they made and the one most likely to have silently not taken.
If they chose `pi`, confirm that Pi will handle all three Claude Code features without Codex.

Then state plainly: which steps ran, what each check returned, which optional parts were skipped, and
anything that warned rather than failed. If you stopped early, say at which step and why — a
truthful partial report is more useful than a confident summary of an install that did not finish.
