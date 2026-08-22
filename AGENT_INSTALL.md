# Installing reviewer-agent unattended

A procedure for an **agent** that has been pointed at this repository and told to install it. Every
step has a command and a check that proves it worked. Do not proceed past a failed check.

This guide installs the **Claude Code** plugin at the repository root. For the Codex-native plugin,
follow [`plugins/reviewer-agent-codex/README.md`](plugins/reviewer-agent-codex/README.md#install);
its installer also performs Codex-specific direct-tool configuration.

If you are a human, [`README.md`](README.md) is shorter.

---

## Read this before running anything

**This installation writes outside the repository.** You are acting on someone's machine, so the
full list is here, before the first command rather than buried in a step:

| path | what happens | reversible? |
|---|---|---|
| Claude Code's plugin directory | the plugin is installed | yes, `/plugin uninstall reviewer-agent` |
| `~/.claude/settings.json` | `askUserQuestionTimeout` is **added if absent**; an existing value is never overwritten | yes, remove the key |
| `~/.claude/reviewer-agent/` | created for flags, session ids, logs, verdicts | yes, delete the directory |
| `~/.pi/agent/extensions/git-read-only/` | **only if** the backend is `pi` | yes, delete the directory |
| `<PLUGIN_DIR>/pi-extensions/git-read-only/node_modules/` | **only if** the backend is `pi` (~131 MB, gitignored) | yes, delete the directory |
| `<TARGET_REPO>/reviewerAgent.md` | created at step 5 **if absent** | yes, delete the file |

**Nothing here overwrites an existing file, and two steps need an explicit check to keep that
true.** Step 5 writes a brief and the optional pi section copies a directory; both are told to
refuse rather than clobber, because a repository's existing `reviewerAgent.md` is hand-written
content nobody wants replaced by a template, and an existing pi extension may be a newer copy than
this one. `askUserQuestionTimeout` is likewise added only when absent — the toggle never replaces
a value you chose.

## Two paths, and they are not the same directory

Later steps move between the plugin checkout and the repository being gated. Fix both up front and
refer to them by name; a command run from the wrong one fails confusingly or writes to the wrong
tree:

- **`<PLUGIN_DIR>`** — this checkout, the one containing `AGENT_INSTALL.md` and `tests/`.
- **`<TARGET_REPO>`** — the repository whose commits you are gating. It may be the same directory,
  and often is not.

Shell commands below carry an explicit `cd`, so no step depends on where the previous one left you.

**Slash commands are different, and this is the trap.** `/reviewerAgent`, `/stopContinue` and the
rest act on the directory **the Claude Code session is running in**. A `cd` inside a Bash step
changes a subprocess, not the session, so it cannot move them. If you run `/reviewerAgent on` from
a session opened in `<PLUGIN_DIR>`, you enable the gate on the plugin checkout and the target
repository stays ungated — with no error, because that is a legitimate thing to ask for.

So: **steps 4 to 7 must be run from a Claude Code session whose working directory is
`<TARGET_REPO>`.** If `<PLUGIN_DIR>` and `<TARGET_REPO>` are the same directory, you are already
there. If they are not, and your session is in `<PLUGIN_DIR>`, finish step 3 there, then open a
session in `<TARGET_REPO>` and continue from step 4. Do not try to work around this from the shell.

There is no status subcommand to check this with beforehand — `/reviewerAgent` takes `on` or `off`
and nothing else. What you get instead is the path in its own success line:

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
operator's backend choice and its audit trade-off); the chosen backend is unauthenticated; `~/.claude/
settings.json` is unwritable; the test suite fails; or any check below does not produce the stated
result. A partially installed fail-closed gate can block every commit in every repository, so a
half-finished install is worse than none.

**Stop if a standalone predecessor is still registered.** Before installing the plugin, inspect
the user hook commands without printing unrelated settings:

```bash
python3 -c "import json,os;p=os.path.expanduser('~/.claude/settings.json');d=json.load(open(p)) if os.path.exists(p) else {};print('\n'.join(f'{event}: {h.get(\"command\", \"\")}' for event,groups in (d.get('hooks') or {}).items() for group in groups for h in (group.get('hooks') or []) if 'revieweragent' in h.get('command','').lower() or h.get('command','').endswith('/bash_gate.py')))"
```

If it prints commands under `~/.claude/reviewerAgent/` or `~/.claude/hooks/bash_gate.py`, stop and
report that the standalone hooks must be migrated before plugin installation. Do not simply delete
`bash_gate.py`: some older installs use it to chain a separate preflight hook that may still be
wanted. The README's upgrade section describes the required inspection. Running both copies causes
duplicate audits and older `SessionStart` hooks launch Codex even when Pi is selected.

---

## Step 1 — confirm the platform

```
uname -s                 # expect: Darwin or Linux
command -v python3       # expect: an executable path
python3 -V               # expect: Python 3.9.x or newer
if test -n "${WSL_DISTRO_NAME:-}" || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
  grep -qi 'WSL2' /proc/sys/kernel/osrelease || { echo "ERROR: WSL2 not detected"; exit 1; }
fi
```

**Check:** the platform, interpreter, version, and conditional WSL2 checks all succeed. macOS is
tested. Native Linux and Windows through WSL2 are an **untested Linux/WSL2 beta** that is expected
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

## Step 2 — detect the backends, then ASK which one to use

There are three, `codex`, `pi` and `claude`. **None is a requirement and you do not get to pick
for them.** Detect what is present, then put the choice to the operator — do not infer it from
what happens to be installed, and do not let the resolution default decide silently.

```
command -v codex || ls ~/.local/bin/codex
command -v pi
command -v claude
```

`claude` is present on every Claude Code host by definition, so its presence is not evidence that
anyone wants it as the reviewer. **Never select it silently**, and always state its cost when you
offer it: the review runs on the operator's own Claude subscription quota, the same allowance
their coding session spends, and an exhausted allowance blocks every commit until it resets.

Now branch on what you found. In every case the operator answers before you continue:

- **More than one available** — **ask which one to use.** State the relevant trade-off: every
  backend handles commit audits, timed-out questions, and stop supervision; *codex* can run the
  test suite during an audit, *pi* is shell-free and runs whatever model pi is configured for, and
  *claude* is shell-free, needs no extra install, and spends the operator's own Claude quota. Do
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

If `codex` lives somewhere non-standard, note the path; it goes in `REVIEWER_CODEX_BIN` at step 6.

## Step 3 — verify the checkout before trusting it

```
cd <PLUGIN_DIR>
python3 -m pytest tests/ -q -p no:cacheprovider
python3 tools/check_split_layering.py
```

**Check:** every test passes, with two expected skips (two state containers have no reset function,
so their reset row skips itself), and the structural check prints
`PASS [VERIFY]: acyclic, zero back-edges`.

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

Hooks are snapshotted per session at start. An installing agent may finish steps 5 and 6 with the
source scripts below, but the human must then restart Claude Code before step 7. A session that was
already running keeps the old hook snapshot, so the gate can appear installed and do nothing.

## Step 5 — write the brief

**This comes before enabling, and not as a matter of taste: `/reviewerAgent on` refuses a
repository that has no brief.** It exits with `reviewerAgent: ERROR - brief not found at <path>`
and changes nothing, so step 6 cannot succeed until this one has.

**Refuse to overwrite an existing brief.** `cp` would silently replace hand-written content with a
template:

```
test -e <TARGET_REPO>/reviewerAgent.md \
  && echo "EXISTS - leave it alone, do not overwrite" \
  || cp <PLUGIN_DIR>/reviewerAgent.md.template <TARGET_REPO>/reviewerAgent.md
```

If it already exists, **report that** and leave the file untouched — it is very likely better than
the template — then continue to step 6.

If you created it, **edit it for that repository**: what the reviewer must enforce, the conventions
it should know, the defect classes this codebase actually produces. Do not leave the template
unedited — a generic brief produces generic reviews, and this step is what determines whether the
tool is worth having.

**Check:** `<TARGET_REPO>/reviewerAgent.md` exists and describes that repository specifically.

## Step 6 — enable it, per repository

**From a Claude Code session whose working directory is `<TARGET_REPO>`** — see *Two paths* above.
The flags are keyed by the repository root, so running these from `<PLUGIN_DIR>` silently gates
the wrong repository.

```
/reviewerAgent on
/stopContinue on          # optional, separate flag
```

An installing agent can perform the same state changes before the restart, from
`<TARGET_REPO>`, without asking the human to type slash commands:

```bash
python3 <PLUGIN_DIR>/toggle.py on
python3 <PLUGIN_DIR>/stopcontinue_toggle.py on  # only when the operator selected it
```

**Check first — the path in the output.** `/reviewerAgent on` replies
`reviewerAgent: ON for <root> ...`. If `<root>` is not `<TARGET_REPO>`, the session is in the wrong
directory: `/reviewerAgent off`, then re-run from a session in `<TARGET_REPO>`. Do not continue
until the reported root is correct — everything after this point would be verifying the wrong
repository.

If instead it prints `reviewerAgent: ERROR - brief not found at <path>`, nothing was enabled: either
step 5 did not run, or the session is in a directory other than the one you wrote the brief in.
Compare the path it names against step 5's file before doing anything else.

**Check:** `/reviewerAgent on` reports that it armed `askUserQuestionTimeout`, or warns that it
could not write `~/.claude/settings.json`. A warning is not fatal for the commit gate, but question
answering will never fire until it is resolved — report it rather than ignoring it.

Verify the setting landed:

```
python3 -c "import json,os;p=os.path.expanduser('~/.claude/settings.json');print(json.load(open(p)).get('askUserQuestionTimeout'))"
```

**Check:** prints a value such as `60s`. If it prints `None`, question answering is inert; run
`python3 <PLUGIN_DIR>/setup_timeout.py 60s` and re-check.

If `codex` is at a non-standard path from step 2, set `REVIEWER_CODEX_BIN` to it now.

## Step 7 — prove the gate actually gates

Do not declare success on configuration alone. Make a trivial commit in the target repository and
confirm a reviewer verdict appears — an approval or a rejection both prove the hook fired.

For an agent-assisted installation, do not manufacture a commit in the operator's repository. The
agent should finish configuration before the human-only restart, report that live gate verification
is pending, and identify the first real commit after restart as the end-to-end check. If the operator
explicitly requests an immediate proof, resume after restart and agree on a harmless test commit
before changing Git history.

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

Pi handles commit audits, timed-out questions, and stop supervision with whatever model Pi is
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
