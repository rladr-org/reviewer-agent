# Claude Code Linux Issues Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (inline; the
> operator does not want sub-agents) to implement this plan task-by-task. Steps use checkbox
> (`- [ ]`) syntax for tracking.

**Goal:** Fix every confirmed defect in `claude-code-issues-found-on-ubuntu-linux.md` (the Claude
Code plugin install/test on Ubuntu 20.04, git 2.25.1, NFS `TMPDIR`), with a regression test for
each and an acceptance run on a real old-git Linux host.

**Architecture:** Each fix is local to the module that owns the behaviour. The one design choice
is #15 (the commit gate is bypassable and has false positives). The operator's order of
preference was:

1. no git hook — fix it inside the PreToolUse gate;
2. only if that proves insufficient, an agent-only git hook that a config setting can turn off,
   with uninstall instructions.

Phase C implements (1). Phase D specifies (2) as an optional phase that runs only on the
operator's explicit go-ahead after Phase C lands.

**Tech Stack:** Python 3.9+ standard library only (runtime); pytest (tests only); POSIX `sh`
(launcher); GitHub Actions; Docker `ubuntu:20.04` for old-git runs.

**Spec:** `claude-code-issues-found-on-ubuntu-linux.md` (repository root, untracked). Item numbers
below (#4, #15, ...) are that report's numbers.

## Verification status (2026-09-25, HEAD 99df41a)

No runtime `.py` file has changed since the report's commit (a9071f1), so its line numbers still
apply. Every item was re-checked against the code:

| # | Status | Notes |
|---|---|---|
| 15 | confirmed | `_COMMIT_RE` (`reviewer_git.py:34`). The module docstring (`:9-14`) says detection is "biased toward precision"; that stance predates the gate-owns-the-commit model and is reversed here. |
| 16 | confirmed | `commit_hook.py:59-61,625-626` has no guard. `session_start_hook.py` has only a partial guard. The stop, askq and UPS hooks already fail open by design. |
| 22 | confirmed | Nothing-staged (`commit_hook.py:128`) runs before `parse_commit_request` (`:184`). The compound-refusal text names the wrong part ("here: git commit -m x"). |
| 17 | confirmed | Retry happens only on an empty read (`reviewer_stopcontinue.py:117-121`). **Claude Code 2.1.283's Stop payload carries `last_assistant_message`** (found in the binary's hook schema); `stop_hook.py` ignores it. |
| 19 | confirmed | The stop prompt (`plugins/reviewer-agent-codex/prompts/stop_supervisor.txt`, shared by both hosts) never names operator-only steps. There is no repeat detection. |
| 18 | confirmed | The hook matches only the sentinel (`userpromptsubmit_hook.py:36`). No test drives the hook with a real prompt. |
| 20 | confirmed | `stopcontinue_watchdog_toggle.py:86` prints a bare ACTIVE/INACTIVE; nothing consumes it (the tick uses Read). |
| 21 | confirmed | `clear_review_history` (`reviewer_stopcontinue.py:374`) leaves `reviews/<stem>.audits.jsonl` and `reviews/prompt_parts/`. Parts are content-addressed and global; ownership is derivable from each project's audit log. |
| 7 | confirmed | `tests/test_reviewer_ignore.py:38` is the only `init -b`. CI hides it: `init.defaultBranch` is set globally and only a modern git is used. |
| 8 | confirmed | Three tests rely on real free space being below 10¹⁵/10¹⁸ bytes. |
| 4 | confirmed | `codex_bin()` has no PATH fallback (`reviewer_state.py:96`). The README backlog line (`:1176`) is stale. |
| 9 | **owned elsewhere** | This is a Codex-plugin bug. It is already fixed in the working tree by the concurrent `docs/plans/codex_linux_issues.md` session. |
| docs 1,3,6,11 | **owned elsewhere** | `INSTALL_WITH_AGENT.md` is being edited by the Codex-plan session (source authentication, venv + dev deps, background run, permanent checkout). |
| docs 2,5,10,13,14,23 | confirmed | `AGENT_INSTALL.md` / `reviewerAgent.md.template` / `pytest.ini`. |
| small | mixed | See Phase E/F and "Not in this plan". "Gate output replaces git's" is already in the README (`:178-181`), so it is dropped. |

Claude-side git usage is nothing newer than git 2.5 (`rev-parse --git-common-dir`). There is no
runtime git floor; only the one test fixture needs 2.28.

## Linux-specific items (where the fix must be verified on Linux)

| # | Linux? | Why | Where to verify |
|---|---|---|---|
| 7 | **yes** | git 2.25.1 is Ubuntu 20.04's git. Every modern image hides the bug. | CI job in an `ubuntu:20.04` container (Task 1); locally via Docker; the AWS host. |
| 16 (launcher) | **yes** | Claude Code started by systemd, cron or an IDE without a login PATH is a Linux-server situation. `/bin/sh` is **dash** on Ubuntu, so the launcher must be strictly POSIX. | Task 4 tests run under `/bin/sh` on the Linux CI job; live check on the AWS host with `env -i PATH=/usr/bin:/bin`. |
| 4 | **yes (typical)** | npm-global `codex` in `/usr/local/bin` is the common Linux install. | AWS host live audit with no `codexBin` configured. |
| 8 | host-environment | NFS/large filesystems report absurd free space. The fix is OS-independent (mocking). | Any OS; confirm on the AWS host's NFS `TMPDIR`. |
| 17 | partly | Slow or remote disks widen the transcript flush race. The fix is OS-independent. | Unit tests anywhere; live on the AWS host if convenient. |
| 15 (Phase D only) | git-version | `pre-merge-commit` needs git ≥2.24 (available on 2.25). `reference-transaction` needs 2.28, which Ubuntu 20.04 lacks. | Only if Phase D runs: Docker `ubuntu:20.04` plus a modern git. |
| all others | no | Pure Python, docs or prompt text. | macOS is fine. |

Local Linux runs need the Docker daemon (Docker Desktop is installed but was **not running** on
2026-09-25): start Docker Desktop before Task 1 Step 5.

## Global Constraints

- Runtime stays **Python 3.9+, standard library only**. Tests may use pytest/pyyaml
  (`requirements-dev.txt`).
- **No git-version requirement** in the plugin or its tests (operator policy). Use nothing newer
  than git 2.25 at runtime or in fixtures.
- The commit gate stays **fail-closed**; stop supervision, AskUserQuestion reroute,
  UserPromptSubmit and SessionStart stay **fail-open**.
- `hooks/hooks.json` keeps the exec form (`command` + `args`) and never hardcodes a
  Python install path.
- Do **not** edit `plugins/reviewer-agent-codex/**` or `INSTALL_WITH_AGENT.md`; the concurrent
  Codex-plan session owns them. The single exception is the shared stop prompt
  `plugins/reviewer-agent-codex/prompts/stop_supervisor.txt` (Task 11), which both hosts load;
  tell that session before editing it.
- New names exported through `reviewer_lib` must be added to its explicit import list, and the
  facade contract regenerated: `python3 tools/gen_facade_contract.py`, then run
  `tests/test_facade_contract.py`.
- Any new shipped file must be included by `tools/release_export.py` (check with
  `tests/test_release_export.py`).
- Per-task commit workflow (operator rule): **red tests → commit; green → commit.** Stage in one
  Bash call and commit in a separate one (the gate accepts exactly one plain `git commit -m`).
  Commit messages are one line, with **no** `Co-Authored-By` trailer.
- Run tests from the repo root: `python3 -m pytest <paths> -q -p no:cacheprovider`.
- Full-suite runs take minutes; use `run_in_background: true`.

## Review Focus

1. **An inactive repo is never touched.** A Bash call in a repo where the gate is off, containing
   `git merge`, `git cherry-pick` or `bash -c 'git commit …'`, must be allowed with no output.
   Pinned in Task 8.
2. **Data that merely mentions a commit is not a commit.** A heredoc that writes a file whose body
   contains `git commit -m x`, run in an **active** repo, must be allowed. Pinned in Task 6.
3. **The launcher passes stdin through byte-exact under dash.** A payload with quotes, newlines,
   `$` and backslashes must reach Python unchanged. Pinned in Task 4.
4. **Directive text is never executed.** `/reviewerDirective note $(touch /tmp/pwned) \`id\`` is
   recorded verbatim and nothing executes. Pinned in Task 13.
5. **Clearing one project's history never deletes another's data.** A prompt part shared with a
   second project survives the first project's `/reviewerHistory clear`. Pinned in Task 15.

---

## Phase A — Tests portable to old git and odd filesystems

### Task 1: Git-version-free test fixture + old-git CI job (#7)

**Files:**
- Modify: `tests/test_reviewer_ignore.py:33-40`
- Modify: `tests/repo_context.py` (add `init_repo`)
- Create: `tests/test_git_version_policy.py`
- Create: `tools/ci_old_git.sh`
- Modify: `.github/workflows/python.yml` (new job `old-git`)

**Interfaces:**
- Produces: `repo_context.init_repo(path, branch="main") -> None`. It creates the repo at
  `path` with HEAD on `refs/heads/<branch>` using only git ≤2.25 features.

The CI job is coordinated with `docs/plans/codex_linux_issues.md` ("Verification and
completion"), which also plans a git 2.25.1 job. If that job has already landed when this task
starts, extend it to run the root suite and skip Step 6 below. If not, this task adds it
(running both suites) and the Codex plan reuses it.

- [ ] **Step 1: Write the failing guard test**

```python
# tests/test_git_version_policy.py
# SPDX-License-Identifier: MPL-2.0
"""Neither the plugin nor its tests may require a particular git version (operator policy).

Ubuntu 20.04 ships git 2.25.1. These flags arrived later and, on 2.25, either print usage
(`init -b`, 2.28) or are echoed back with exit 0 (`--path-format`, 2.31) -- the second silently
corrupts a result instead of failing."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_POST_225 = re.compile(r"""init["',\s-]+(?:q["',\s]+)?-b\b|--initial-branch|--path-format""")


def test_no_file_uses_git_flags_newer_than_2_25():
    offenders = []
    for path in list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")) + \
            list((ROOT / "tools").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _POST_225.search(line):
                offenders.append("{}:{}: {}".format(path.relative_to(ROOT), number, line.strip()))
    assert not offenders, "git >2.25 flags found:\n" + "\n".join(offenders)
```

- [ ] **Step 2: Run it to confirm red**

Run: `python3 -m pytest tests/test_git_version_policy.py -q -p no:cacheprovider`
Expected: FAIL listing `tests/test_reviewer_ignore.py:38`.

- [ ] **Step 3: Commit red**

```bash
git add tests/test_git_version_policy.py
```
```bash
git commit -m "test: forbid git flags newer than 2.25 in the plugin and its tests"
```

- [ ] **Step 4: Implement the helper and fix the fixture**

Append to `tests/repo_context.py`:

```python
def init_repo(path, branch="main"):
    """`git init` with HEAD on `branch`, without `init -b` (git 2.28+): Ubuntu 20.04 ships 2.25.

    `symbolic-ref` on an unborn HEAD has worked since the earliest gits, so this names the
    branch the same way on every version, independent of `init.defaultBranch`."""
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "symbolic-ref", "HEAD",
                    "refs/heads/" + branch], check=True)
```

(Add `import subprocess` at the top of `repo_context.py` if it is not already imported.)

Replace `tests/test_reviewer_ignore.py:38`:

```python
    from repo_context import init_repo
    init_repo(proj)
```

- [ ] **Step 5: Add the old-git runner and run it locally on Linux**

```sh
#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# tools/ci_old_git.sh -- run the root suite on Ubuntu 20.04's git 2.25.1 and Python 3.9.
# CI runs it in an ubuntu:20.04 container; locally:
#   docker run --rm -v "$PWD":/src -w /src ubuntu:20.04 sh tools/ci_old_git.sh
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3.9 python3.9-venv >/dev/null
git --version
case "$(git --version)" in
  "git version 2.25."*) ;;
  *) echo "expected git 2.25.x" >&2; exit 1 ;;
esac
git config --global --add safe.directory "$PWD"
git config --global user.email ci@example.com
git config --global user.name CI
# Deliberately NO init.defaultBranch: the suites must not depend on it.
python3.9 -m venv /tmp/venv
/tmp/venv/bin/python -m pip install -q -r requirements-dev.txt
/tmp/venv/bin/python -m pytest -p no:cacheprovider -q
```

Run (Docker Desktop started): `docker run --rm -v "$PWD":/src -w /src ubuntu:20.04 sh tools/ci_old_git.sh`
Expected: `git version 2.25.1`. Nothing in `test_reviewer_ignore.py` fails. Any other failure is
a real old-git or no-`defaultBranch` dependency: fix it here and add a line to the guard regex if
it is a flag.

- [ ] **Step 6: Add the CI job**

Append under `jobs:` in `.github/workflows/python.yml`:

```yaml
  old-git:
    # Ubuntu 20.04's git 2.25.1. GitHub retired the ubuntu-20.04 runner, so run its image as a
    # container. Nothing here may require a newer git (operator policy).
    name: root suite on git 2.25 (ubuntu:20.04)
    runs-on: ubuntu-latest
    container: ubuntu:20.04
    steps:
      - uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4
      - name: Root suite on old git
        run: sh tools/ci_old_git.sh
```

- [ ] **Step 7: Run the guard + ignore tests, then commit green**

Run: `python3 -m pytest tests/test_git_version_policy.py tests/test_reviewer_ignore.py -q -p no:cacheprovider`
Expected: PASS.

```bash
git add tests/repo_context.py tests/test_reviewer_ignore.py tools/ci_old_git.sh .github/workflows/python.yml
```
```bash
git commit -m "fix: build test repos without init -b and run the root suite on git 2.25 in CI"
```

### Task 2: Low-disk tests independent of the host's free space (#8)

**Files:**
- Modify: `tests/test_scratch_cleanup.py:165-181`
- Modify: `tests/test_commit_hook.py:287-309`

There is no production change and no test-only environment hook. The in-process tests fake
`shutil.disk_usage`. The subprocess test uses a threshold no filesystem can reach: 64-bit block
counts × block size stay below 2⁸⁴ ≈ 1.9×10²⁵.

- [ ] **Step 1: Rewrite the three tests**

In `tests/test_scratch_cleanup.py`, add this helper above the section and replace the two tests
that set `10 ** 15`:

```python
def _fake_free(monkeypatch, free_bytes):
    """Report `free_bytes` free on every path. An NFS mount reported 8 EiB free (2026-09 Ubuntu
    host), so a merely "impossibly high" threshold is not a test of anything."""
    import reviewer_state
    real = reviewer_state.shutil.disk_usage
    monkeypatch.setattr(reviewer_state.shutil, "disk_usage",
                        lambda p: real(p)._replace(free=free_bytes))


def test_low_disk_warning_warns_below_threshold(scratch_env, monkeypatch):
    _dir_for("x", create=True)  # ensure the root exists
    monkeypatch.setenv("REVIEWER_DISK_WARN_BYTES", str(10 ** 9))
    _fake_free(monkeypatch, 10 ** 6)
    w = rl.low_disk_warning()
    assert w and "low disk" in w.lower()


def test_low_disk_warning_missing_root_does_not_raise(scratch_env, monkeypatch):
    monkeypatch.setenv("REVIEWER_DISK_WARN_BYTES", str(10 ** 9))
    _fake_free(monkeypatch, 10 ** 6)
    # Root does not exist (no dir created): must fall back to an existing ancestor, never raise.
    assert rl.low_disk_warning() is not None
```

In `tests/test_commit_hook.py:287-309`, change the environment value `str(10**18)` to
`str(10**30)` and add the comment
`# above any value statvfs can report (block counts are 64-bit; x block size < 2**84)`.

- [ ] **Step 2: Run them**

Run: `python3 -m pytest tests/test_scratch_cleanup.py tests/test_commit_hook.py -k low_disk -q -p no:cacheprovider`
Expected: PASS here. They also pass with `TMPDIR` on an NFS mount (check in Task 21).

- [ ] **Step 3: Commit**

The tests already existed and were wrong, so this is a single commit.

```bash
git add tests/test_scratch_cleanup.py tests/test_commit_hook.py
```
```bash
git commit -m "test: fake disk usage instead of assuming the host has less than 1 PB free"
```

---

## Phase B — The gate fails closed, and says why

### Task 3: Top-level guard on the commit hook and SessionStart (#16)

**Files:**
- Modify: `commit_hook.py` (module import guard, `main(raw=None)`, the `__main__` block, and the
  `tool_input` null-safety at `:65`)
- Modify: `session_start_hook.py:69-89` (wrap the whole body)
- Test: `tests/test_commit_hook.py` (new tests at the end)

**Interfaces:**
- Produces: `commit_hook.main(raw=None) -> int`. When `raw` is None it reads stdin (the existing
  `main()` callers are unchanged).
- Produces: `commit_hook._crash_output(exc, raw) -> dict | None`. It returns a PreToolUse deny
  when the payload mentions `commit`, else None.

Policy: a crash denies **only** payloads whose text contains `commit`. The hook runs on every
Bash call in every project, even where the gate is off. A crash-deny on everything would disable
Bash host-wide, and it cannot consult gate state when `reviewer_lib` itself failed to import.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_commit_hook.py
import pytest


@pytest.mark.parametrize("stdin_text", [
    '{not json, "git commit -m x"',          # JSONDecodeError
    json.dumps(["git commit -m x"]),          # not an object
])
def test_a_crash_on_a_commit_payload_denies_instead_of_exiting_nonzero(tmp_path, stdin_text):
    # Claude Code blocks only on exit 2 or an explicit deny; exit 1 lets the call run.
    p = subprocess.run([sys.executable, COMMIT], input=stdin_text,
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "crashed" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_a_crash_on_an_unrelated_payload_stays_out_of_the_way(tmp_path):
    p = subprocess.run([sys.executable, COMMIT], input='{not json, "ls -la"',
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_tool_input_null_is_not_a_crash(tmp_path):
    p = run_hook({"cwd": str(tmp_path), "tool_input": None}, {})
    assert p.returncode == 0 and p.stdout.strip() == ""
```

Add a SessionStart test to `tests/test_session_start_hook.py`:

```python
def test_session_start_never_exits_nonzero_on_bad_input(tmp_path):
    p = subprocess.run([sys.executable, SESSION_START], input="{not json",
                       capture_output=True, text=True,
                       env=dict(os.environ, REVIEWER_HOME=str(tmp_path / "rh")))
    assert p.returncode == 0
```

(Use that file's existing `SESSION_START` path constant; if its name differs, use the one it
defines.)

- [ ] **Step 2: Run them to confirm red**

Run: `python3 -m pytest tests/test_commit_hook.py -k "crash or null" tests/test_session_start_hook.py -k bad_input -q -p no:cacheprovider`
Expected: FAIL (exit code 1 with a traceback).

- [ ] **Step 3: Commit red**

```bash
git add tests/test_commit_hook.py tests/test_session_start_hook.py
```
```bash
git commit -m "test: pin the commit hook failing closed on a crash instead of exiting 1"
```

- [ ] **Step 4: Implement**

In `commit_hook.py`, wrap the existing `import reviewer_lib as rl` line:

```python
try:
    import reviewer_lib as rl
except Exception as _exc:  # reported by the __main__ guard, which must not depend on rl
    rl = None
    _IMPORT_ERROR = _exc
else:
    _IMPORT_ERROR = None
```

Change the head of `main`:

```python
def main(raw=None):
    if raw is None:
        raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("hook input must be a JSON object")

    # Cheap precision check FIRST so the canonical-root resolution (a git subprocess)
    # runs only for actual commits, never on every Bash command.
    command = (data.get("tool_input") or {}).get("command") or ""
```

Replace the `__main__` block:

```python
def _crash_output(exc, raw):
    """The fail-CLOSED answer when the gate itself failed, or None to stay out of the way.

    Claude Code treats any exit status other than 2 as a NON-blocking error, so an escaped
    exception used to let the commit run unreviewed. A crash cannot consult the gate's state
    (reviewer_lib may be what failed), and this hook runs on every Bash call in every project,
    so it denies only calls whose payload mentions a commit."""
    if "commit" not in (raw or ""):
        return None
    reason = (
        "reviewerAgent's commit gate crashed ({}: {}) before it could decide, so this call is "
        "blocked rather than let a commit through unreviewed. Retry; if it repeats, see "
        "~/.claude/reviewer-agent/logs/reviewer.log.".format(type(exc).__name__, exc)
    )
    return {
        "systemMessage": "[reviewerAgent] commit gate crashed; call blocked (fail-closed).",
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


if __name__ == "__main__":
    _raw = ""
    try:
        _raw = sys.stdin.read()
        if _IMPORT_ERROR is not None:
            raise _IMPORT_ERROR
        _code = main(_raw)
    except Exception as _exc:
        try:
            import traceback
            if rl is not None:
                rl.log("[commit] EXCEPTION {} -> {}\n{}".format(
                    type(_exc).__name__,
                    "denied" if "commit" in _raw else "allowed (no commit in payload)",
                    rl.tail(traceback.format_exc(), 800)))
        except Exception:
            pass
        _out = _crash_output(_exc, _raw)
        if _out is not None:
            print(json.dumps(_out))
        _code = 0
    raise SystemExit(_code)
```

In `session_start_hook.py`, move the whole body of `main()` (from `raw = sys.stdin.read()` down to
the final print) inside one `try:`, with this handler:

```python
    except Exception as e:
        try:
            rl.log("[session_start] EXCEPTION {}: {}".format(type(e).__name__, e))
        except Exception:
            pass
        return 0
```

Keep the existing inner guards around `sweep_orphan_scratch_dirs` / `_brief_if_active`.

- [ ] **Step 5: Run the gate suites**

Run: `python3 -m pytest tests/test_commit_hook.py tests/test_gate_owns_commit.py tests/test_session_start_hook.py -q -p no:cacheprovider`
Expected: PASS.

- [ ] **Step 6: Commit green**

```bash
git add commit_hook.py session_start_hook.py
```
```bash
git commit -m "fix: deny a commit when the gate crashes instead of exiting 1 and letting it run"
```

### Task 4: A launcher that finds Python, and fails closed without it (#16, Linux)

**Files:**
- Create: `hooks/run_hook.sh`
- Modify: `hooks/hooks.json` (all five entries)
- Modify: `tests/test_repository_distribution.py:60-84` (the exec-form assertion)
- Modify: `reviewer_model_toggle.py` `_hook_timeout_ceiling` (if it only reads `args[0]`)
- Test: `tests/test_hook_launcher.py` (new)

**Interfaces:**
- Produces: the hooks.json entry shape
  `{"command": "/bin/sh", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh", "${CLAUDE_PLUGIN_ROOT}/<script>.py"]}`.
- Produces: the environment variable `REVIEWER_HOOK_PYTHONS` (space-separated candidate list).
  It exists for tests and for operators with an unusual Python.

`/bin/sh` is referenced by absolute path, so the launcher itself never depends on PATH. On Ubuntu
`/bin/sh` is dash: no bashisms.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hook_launcher.py
# SPDX-License-Identifier: MPL-2.0
"""hooks/run_hook.sh: find a Python even without a login PATH; fail the commit gate CLOSED
(exit 2) when there is none. Claude Code launched by systemd/cron/an IDE is the real case."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = str(ROOT / "hooks" / "run_hook.sh")


def _run(script, payload, pythons, extra_env=None):
    env = {"PATH": "/nonexistent", "REVIEWER_HOOK_PYTHONS": pythons,
           "HOME": os.environ.get("HOME", "/tmp")}
    env.update(extra_env or {})
    return subprocess.run(["/bin/sh", LAUNCHER, str(ROOT / script)], input=payload,
                          capture_output=True, text=True, env=env)


def test_commit_gate_blocks_with_exit_2_when_no_python_exists():
    p = _run("commit_hook.py", json.dumps({"tool_input": {"command": "git commit -m x"}}),
             "/nonexistent/python3")
    assert p.returncode == 2
    assert "python3" in p.stderr


def test_commit_gate_stays_out_of_the_way_for_non_commits_without_python():
    p = _run("commit_hook.py", json.dumps({"tool_input": {"command": "ls"}}),
             "/nonexistent/python3")
    assert p.returncode == 0 and p.stdout == ""


def test_session_start_warns_loudly_without_python():
    p = _run("session_start_hook.py", "{}", "/nonexistent/python3")
    assert p.returncode == 0
    assert "no python3" in json.loads(p.stdout)["systemMessage"]


def test_stdin_reaches_python_byte_exact(tmp_path):
    # Review Focus 3: quotes, newlines, $, backslashes, under dash on Linux.
    probe = tmp_path / "echo_stdin.py"
    probe.write_text("import sys; sys.stdout.write(sys.stdin.read())\n")
    payload = json.dumps({"tool_input": {"command": "printf '%s\\n' \"$HOME\" `id` \\\\ \n x"}})
    p = subprocess.run(["/bin/sh", LAUNCHER, str(probe)], input=payload, capture_output=True,
                       text=True, env={"PATH": "/nonexistent",
                                       "REVIEWER_HOOK_PYTHONS": sys.executable})
    assert p.returncode == 0 and p.stdout == payload
```

- [ ] **Step 2: Run them to confirm red**

Run: `python3 -m pytest tests/test_hook_launcher.py -q -p no:cacheprovider`
Expected: FAIL (`run_hook.sh` does not exist).

- [ ] **Step 3: Commit red**

```bash
git add tests/test_hook_launcher.py
```
```bash
git commit -m "test: pin a hook launcher that fails the commit gate closed without python3"
```

- [ ] **Step 4: Implement the launcher**

```sh
#!/bin/sh
# SPDX-License-Identifier: MPL-2.0
# reviewer-agent hook launcher. Claude Code started outside a login shell (systemd, cron, an IDE)
# may not have python3 on its PATH; a bare `python3` then exits 127, which Claude Code treats as
# a NON-blocking error -- every hook, including the fail-closed commit gate, silently no-ops.
# POSIX sh only: /bin/sh is dash on Debian/Ubuntu.
script="$1"
shift
for py in ${REVIEWER_HOOK_PYTHONS:-python3 /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3}; do
  if command -v "$py" >/dev/null 2>&1; then
    exec "$py" "$script" "$@"
  fi
done
case "$script" in
  */commit_hook.py)
    payload=$(cat)
    case "$payload" in
      *commit*)
        echo "reviewer-agent: no python3 on the hook PATH, so the commit gate cannot review this call and blocks it (fail-closed). Put python3 on the PATH Claude Code is started with." >&2
        exit 2 ;;
    esac ;;
  */session_start_hook.py)
    echo '{"systemMessage":"[reviewer-agent] no python3 on the hook PATH: the commit gate blocks every command mentioning a commit, and stop supervision is OFF. Put python3 on the PATH Claude Code is started with, then restart."}'
    ;;
esac
exit 0
```

Rewrite each `hooks/hooks.json` entry from `"command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/X.py"]`
to `"command": "/bin/sh", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh", "${CLAUDE_PLUGIN_ROOT}/X.py"]`.
Keep every timeout unchanged.

Update `tests/test_repository_distribution.py:77-84`:

```python
    assert {hook["args"][-1].rsplit("/", 1)[-1] for hook in command_hooks} == expected_scripts
    for hook in command_hooks:
        assert hook["command"] == "/bin/sh"
        assert hook["args"] == [
            "${CLAUDE_PLUGIN_ROOT}/hooks/run_hook.sh",
            f"${{CLAUDE_PLUGIN_ROOT}}/{hook['args'][-1].rsplit('/', 1)[-1]}",
        ]
```

Open `reviewer_model_toggle._hook_timeout_ceiling`. If it matches `commit_hook.py` only against
`args[0]`, change it to match any element of `args`. Add a case to
`test_the_ceiling_reads_the_exec_form_the_manifest_actually_uses` with
`"command": "/bin/sh", "args": ["/p/hooks/run_hook.sh", "/p/commit_hook.py"]`.

- [ ] **Step 5: Run the affected suites**

Run: `python3 -m pytest tests/test_hook_launcher.py tests/test_repository_distribution.py tests/test_reviewer_model_toggle.py tests/test_askq_reroute_hook.py tests/test_stopcontinue_delay.py tests/test_release_export.py -q -p no:cacheprovider`
Expected: PASS. If `test_release_export` fails, add `hooks/run_hook.sh` to the export manifest in
`tools/release_export.py`, and make sure its executable bit is irrelevant (it is always invoked as
`/bin/sh run_hook.sh`).

- [ ] **Step 6: Commit green**

```bash
git add hooks/run_hook.sh hooks/hooks.json tests/test_repository_distribution.py reviewer_model_toggle.py tests/test_reviewer_model_toggle.py tools/release_export.py
```
```bash
git commit -m "fix: launch hooks through a POSIX shim that finds python3 and fails the gate closed without it"
```

### Task 5: Refuse "stage and commit in one call" accurately (#22)

**Files:**
- Modify: `reviewer_git.py` `parse_commit_request` (the shell-operator branch at `:453-462`)
- Modify: `commit_hook.py` (move the `parse_commit_request` block from `:180-194` to just before
  `staged = rl.staged_files(cwd)` at `:128`)
- Test: `tests/test_commit_request.py`, `tests/test_gate_owns_commit.py`

`commits_beyond_index` keeps running first: its `-a`/pathspec message is more specific than
parse's.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commit_request.py
def test_staging_in_the_same_call_gets_an_accurate_refusal():
    error = _refusal("git add f.py && git commit -m x")
    assert "stages and commits in one call" in error
    assert "git add f.py" in error
    assert "(here: git commit" not in error  # the old text named the COMMIT as the dropped part


def test_the_refusal_names_the_parts_that_would_be_dropped():
    error = _refusal("git commit -m x && git push")
    assert "git push" in error and "git commit -m x" not in error.split("(here:")[-1]
```

```python
# tests/test_gate_owns_commit.py
def test_stage_and_commit_in_one_call_is_refused_accurately_with_an_empty_index(
        tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _git(repo, "reset", "-q")  # nothing staged: the case that said "nothing is staged"
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)

    out = _run(repo, "git add f.py && git commit -m x", capsys, monkeypatch)

    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "stages and commits in one call" in reason
    assert "nothing is staged" not in reason
    assert called == []
```

- [ ] **Step 2: Run them to confirm red, then commit red**

Run: `python3 -m pytest tests/test_commit_request.py tests/test_gate_owns_commit.py -k "same_call or dropped or one_call" -q -p no:cacheprovider`
Expected: FAIL.

```bash
git add tests/test_commit_request.py tests/test_gate_owns_commit.py
```
```bash
git commit -m "test: pin an accurate refusal for staging and committing in one call"
```

- [ ] **Step 3: Implement**

In `parse_commit_request`, replace the `for index, token in enumerate(tokens):` operator loop:

```python
    if any(token in _SHELL_OPS or _REDIR_RE.match(token) for token in tokens):
        segments, current = [], []
        for token in tokens:
            if token in _SHELL_OPS or _REDIR_RE.match(token):
                segments.append(current)
                current = []
            else:
                current.append(token)
        segments.append(current)
        others = [" ".join(s) for s in segments
                  if s and not (s[0] == "git" and "commit" in s[1:])]
        staging = [o for o in others if re.match(r"git (?:add|rm|mv|stage)\b", o)]
        if staging:
            return None, (
                "this call stages and commits in one call ({}), but the gate reads the index "
                "BEFORE the call runs, so the staging would never happen first. Run `{}` as its "
                "own tool call, then a plain `git commit -m ...`".format(
                    " && ".join(staging), staging[0])
            )
        return None, (
            "the gate performs the commit itself and then declines this tool call, so "
            "anything else in the call would be dropped without running{}. Send only the "
            "`git commit`, and run the rest as its own tool call.".format(
                " (here: {})".format("; ".join(others)) if others else ""
            )
        )
```

In `commit_hook.py`, cut the block that starts at the comment `# The gate performs the reviewed
commit ITSELF and then DENIES the tool call` and ends at its `return 0` (currently `:180-194`).
Paste it immediately **before** `staged = rl.staged_files(cwd)`. It needs only `key` and
`command`, which are both defined earlier.

- [ ] **Step 4: Run the gate suites**

Run: `python3 -m pytest tests/test_commit_request.py tests/test_gate_owns_commit.py tests/test_commit_hook.py -q -p no:cacheprovider`
Expected: PASS. If an existing `test_commit_hook.py` test pinned the nothing-staged message for a
command that is also a malformed shape, update it to expect the parse message: that is the
intended reordering.

- [ ] **Step 5: Commit green**

```bash
git add reviewer_git.py commit_hook.py
```
```bash
git commit -m "fix: refuse a staged-and-committed-in-one-call command by its real cause, before the index check"
```

---

## Phase C — #15 without a git hook: a commit-intent classifier in the gate

The gate owns the commit: `parse_commit_request` lets exactly one plain `git commit` through and
refuses everything else. A detector that errs toward "this might commit" therefore **fails
closed**: at worst it causes an accurate refusal, never a hijacked command. The fixes are:

1. **False positives:** look only at real command positions, and never inside quoted arguments,
   comments or heredoc bodies that are data.
2. **False negatives:** resolve `git` by basename; unwrap `env`/`command`/`nohup`/`xargs`/…, `bash -c`,
   `eval` and a heredoc fed to a shell; expand `-c alias.*` and repository aliases; follow a literal
   `cd`; catch other commit-producing subcommands; flag inline interpreter scripts that run
   `git commit`.
3. **Target certainty:** a `cd` moves a later commit only when the shell must have run it and
   stayed in that directory; subshells, pipelines and background jobs never move it, and any
   `cd` whose effect is uncertain makes the target unknown (fail closed).
4. **Documented residual gaps:** a script file (run or sourced), a variable holding a whole
   command, an inline script that assembles the command at run time, `git rebase`, and plumbing
   run from a script.

Detection lives in `reviewer_git.py`, the existing git layer: stdlib-only, with no package
imports. Task 6 builds the classifier without rewiring the hook. Task 8 switches the hook over, so
the repository never has a window where detection is narrower than today's regex.

### Task 6: `commit_intent` — command positions, quotes, heredocs, substitutions, `cd`

**Files:**
- Modify: `reviewer_git.py` (new section after `commits_beyond_index`; update the module docstring
  `:9-14`)
- Modify: `reviewer_lib.py` (export `commit_intent`, `CommitIntent`), then regenerate the facade
  contract
- Test: `tests/test_commit_intent.py` (new)

**Interfaces:**
- Produces: `CommitIntent = namedtuple("CommitIntent", "kind target reason")`.
  - `kind` is `None` (no commit-producing git), `"commit"` (a `git commit` for the gate), or
    `"other"` (a commit-producing form the gate refuses).
  - `target` is the directory the invocation acts on, or `None` when it is a shell expansion the
    gate cannot know.
  - `reason` is a refusal text (only for `"other"`).
- Produces: `commit_intent(command, cwd, _depth=0) -> CommitIntent`.
- Produces: `_classify_segment(words, here, body, depth, home) -> (CommitIntent, new_here)`,
  `_classify_git(args, here, depth, home, overrides=None) -> CommitIntent` (`home` is the HOME
  to resolve `~` with, or None when it cannot be trusted; `overrides` holds the invocation's
  `-c key=value` pairs under normalized keys), `_strip_prefixes(words, here, home)`,
  `_join_target(here, value, home)`, `_cd(here, args, home, pushd=False)` and
  `_subcommand_intent(sub, rest, target, overrides) -> CommitIntent`. These are internal; Tasks 7
  and 8 extend them.

**Directory tracking.** A `cd` moves the target of a LATER commit only when the shell is certain
to have changed directory by then. Anything else makes the target `None`, which the hook treats
as unknowable and fails closed on (Task 8):
- A `cd` inside `( … )`, a pipeline element or a background job runs in a subshell: the outer
  directory is restored. shlex cannot be used to find these, because it glues adjacent operators
  (`);`, `))`) into one token and returns a quoted `)` exactly like a real one; `_shell_tokens`
  reports an operator only when it is unquoted.
- Inside one `&&` chain from its start, a later command runs only if the `cd` succeeded, so the
  new directory is certain.
- Across `;` or a newline, the `cd` must have run unconditionally (first in its list) and its
  directory must exist; a `cd` after `||`/`&&`, before `||`, or inside `if`/`while`/`for`/`case`/
  `{ }` may not have run.
- `pushd`/`popd`, `cd -`, a directory-stack tilde, a glob, a `$` expansion or a `CDPATH` lookup
  cannot be resolved from the text, and a `cd` with an unknown option, more than one operand, or
  a directory it cannot search fails without moving.
- `~` and a bare `cd` use the hook's HOME only in the first command of a top-level call, and
  not under a `HOME=` prefix: any earlier command could have changed HOME.
- A line ending in `&&`, `||` or `|` continues onto the next line, and a newline inside
  `$(…)` or backquotes is not a command separator; `_split_script` honours both, so a chain is
  never cut in two.
- `GIT_DIR=`, `GIT_WORK_TREE=`, `GIT_COMMON_DIR=`, `GIT_CONFIG*=`, `--git-dir`, `--work-tree` and
  `--config-env` point git at a repository or configuration the directory does not name. A
  prefix such as `env -C dir` or `GIT_DIR=…` applies to its own command only.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_commit_intent.py
# SPDX-License-Identifier: MPL-2.0
"""Which Bash calls contain a commit-producing git invocation (#15).

Biased toward DETECTION: under the gate-owns-the-commit model a detected non-plain form is
refused with an accurate message (fail-closed), while a missed one commits unreviewed."""
import os

import pytest

import reviewer_lib as rl

CWD = "/work/repo"


def _kind(command, cwd=CWD):
    return rl.commit_intent(command, cwd).kind


@pytest.mark.parametrize("command", [
    'echo "remember to git commit here"',
    "echo 'git commit -m x'",
    "printf '%s\\n' \"git commit\"",
    "cat > notes.md <<'EOF'\nrun git commit -m x afterwards\nEOF",
    "cat > notes.md <<EOF\ngit commit -m x\nEOF\necho done",
    "grep -n 'git commit' README.md",
    "# git commit -m x\nls",
    "git log --grep 'git commit'",
    "git committed",
    "gitk commit",
])
def test_text_that_only_mentions_a_commit_is_not_one(command):
    # Review Focus 2: data is not a command.
    assert _kind(command) is None


@pytest.mark.parametrize("command", [
    "git commit -m x",
    "cd sub && git commit -m x",
    "ls\ngit commit -m x",
    "git status; git commit -m x",
    "(git commit -m x)",
    "echo \"$(git commit -m x)\"",
    "echo `git commit -m x`",
    "FOO=1 git commit -m x",
    "git -C /work/repo commit -m x",
    "git --no-pager -C . commit -m x",
    "if true; then git commit -m x; fi",
])
def test_real_commit_positions_are_found(command):
    assert _kind(command) == "commit"


def test_a_literal_cd_moves_the_target():
    assert rl.commit_intent("cd /other/repo && git commit -m x", CWD).target == "/other/repo"


def test_a_relative_cd_is_joined_to_the_session_cwd():
    assert rl.commit_intent("cd sub && git commit -m x", CWD).target == os.path.join(CWD, "sub")


def test_an_expanded_cd_or_c_target_is_unknowable():
    assert rl.commit_intent('cd "$R" && git commit -m x', CWD).target is None
    assert rl.commit_intent('git -C "$R" commit -m x', CWD).target is None


@pytest.mark.parametrize("command", [
    "(cd /inactive); git commit -m x",       # shlex glues `);` into one token
    "(cd /inactive)\ngit commit -m x",
    "( cd /inactive ) && git commit -m x",
    "((cd /inactive)) && git commit -m x",
    "cd /inactive | true; git commit -m x",  # a pipeline element runs in a subshell
    "true | cd /inactive; git commit -m x",
    "cd /inactive & git commit -m x",        # so does a background job
    "env -C /inactive true; git commit -m x",  # a wrapper's directory is its command's only
    "x=$(\ncd /inactive\n); git commit -m x",  # a newline inside $( ) is not a top-level one
])
def test_a_cd_in_a_subshell_does_not_move_a_later_commit(command):
    # From an active repo, a cd the commit never sees must not retarget it to /inactive.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target == CWD, command


def test_a_cd_inside_the_subshell_moves_the_commit_inside_it():
    assert rl.commit_intent("(cd /other && git commit -m x)", CWD).target == "/other"


@pytest.mark.parametrize("command", [
    "cd /no/such/dir; git commit -m x",       # a failed cd leaves the shell where it was
    "cd /no/such/dir\ngit commit -m x",
    "cd /tmp || exit 1; git commit -m x",     # the commit's directory depends on cd's outcome
    "true ||\ncd /tmp; git commit -m x",      # `||` at a line end continues the chain: cd skipped
    "cd /tmp extra; git commit -m x",         # too many operands: cd fails and stays put
    "cd -X /tmp && git commit -m x",          # an unknown option: cd fails
    "if false; then cd /tmp; fi; git commit -m x",
    "for d in a; do cd /tmp; done; git commit -m x",
    "cd -P ~+ && git commit -m x",            # a directory-stack tilde expanduser cannot resolve
    "pushd /tmp; popd; git commit -m x",
])
def test_a_cd_whose_effect_is_uncertain_makes_the_target_unknowable(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


@pytest.mark.skipif(os.geteuid() == 0, reason="root can enter any directory")
def test_a_cd_into_an_unsearchable_directory_makes_the_target_unknowable(tmp_path):
    # isdir() is True, but `cd` fails without search permission and the shell stays put.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o600)
    try:
        intent = rl.commit_intent("cd {}; git commit -m x".format(locked), CWD)
        assert intent.kind == "commit" and intent.target is None
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("command", [
    "HOME=/active cd; git commit -m x",       # the builtin reads the prefixed HOME
    "HOME=/active cd ~ && git commit -m x",
    "export HOME=/active; cd ~ && git commit -m x",
    'declare "HO""ME=/active"; cd && git commit -m x',
    "true; git -C ~/repo commit -m x",        # not the first command: HOME may have changed
])
def test_a_home_that_may_have_changed_is_not_trusted(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


@pytest.mark.parametrize("home", [None, ""])
def test_an_unset_or_empty_home_is_not_trusted(monkeypatch, home):
    # bash: a bare `cd` with HOME unset fails and stays put; Python would use the passwd home.
    if home is None:
        monkeypatch.delenv("HOME", raising=False)
    else:
        monkeypatch.setenv("HOME", home)
    for command in ("cd; git commit -m x", "cd ~ && git commit -m x"):
        intent = rl.commit_intent(command, CWD)
        assert intent.kind == "commit" and intent.target is None, command


def test_the_first_command_of_a_call_resolves_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/x")
    assert rl.commit_intent("git -C ~/repo commit -m x", CWD).target == "/home/x/repo"
    assert rl.commit_intent("cd ~/repo && git commit -m x", CWD).target == "/home/x/repo"


def test_a_cd_to_an_existing_directory_moves_a_later_commit(tmp_path):
    for sep in ("; ", "\n", " && ", " &&\n"):
        command = "cd {}{}git commit -m x".format(tmp_path, sep)
        assert rl.commit_intent(command, CWD).target == str(tmp_path), repr(sep)
    assert rl.commit_intent("cd -P -- {} && git commit -m x".format(tmp_path),
                            CWD).target == str(tmp_path)


@pytest.mark.parametrize("command", [
    "GIT_DIR=/other/.git git commit -m x",
    "GIT_WORK_TREE=/other git commit -m x",
    "git --git-dir=/other/.git commit -m x",
    "git --work-tree /other commit -m x",
])
def test_a_repository_named_outside_the_directory_is_unknowable(command):
    # The commit lands in a repository the cwd does not name; the gate must not gate cwd.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


def test_an_unparseable_line_falls_back_to_the_old_regex():
    # Unterminated quote: shlex raises; the regex still sees a commit (fail-closed).
    assert _kind("git commit -m 'oops") == "commit"
```

- [ ] **Step 2: Run them to confirm red, then commit red**

Run: `python3 -m pytest tests/test_commit_intent.py -q -p no:cacheprovider`
Expected: FAIL (`commit_intent` is not defined).

```bash
git add tests/test_commit_intent.py
```
```bash
git commit -m "test: pin commit-intent detection by command position rather than raw text"
```

- [ ] **Step 3: Implement**

Replace the docstring paragraph at `reviewer_git.py:9-14` with:

```python
Two jobs, and the split between them matters. The `git ...` LEXICAL parsers decide what a
command would do before it runs: which repository it targets, and whether it would commit
content the staged-only audit never saw. Since the gate performs the reviewed commit itself and
refuses every other shape, detection is biased toward DETECTION: a false positive now costs an
accurate refusal, while a false negative commits unreviewed. `commit_intent` is the detector;
unparseable input falls back to the conservative `_COMMIT_RE`. The `subprocess` helpers then ask
git itself what is true.
```

Add `from collections import namedtuple` to the imports. Then add this section after
`commits_beyond_index`:

```python
# --------------------------------------------------------------------------
# commit intent: does this Bash call run a commit-producing git anywhere? (#15)
# --------------------------------------------------------------------------
CommitIntent = namedtuple("CommitIntent", "kind target reason")
_NO_INTENT = CommitIntent(None, None, None)
_MAX_DEPTH = 4
# Operators a shell recognises only when UNQUOTED, longest first so `&&` wins over `&`.
_OPERATORS = ("<<<", "<<-", "&>>", "&&", "||", ";;", "|&", "<<", ">>", "<&", ">&", "&>", "<>",
              ">|", "(", ")", ";", "|", "&", "<", ">")
_SEGMENT_OPS = _SHELL_OPS | {";;"}
_LIST_ENDS = {None, ";", ";;", "&"}
_PIPES = {"|", "|&"}
_COMPOUND_OPEN = {"if", "while", "until", "for", "case", "select", "{"}
_COMPOUND_CLOSE = {"fi", "done", "esac", "}"}
_ASSIGNMENT_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*=")
# Environment that points git at a repository or configuration the command text does not name.
_GIT_ENV_RE = re.compile(r"\AGIT_(?:DIR|WORK_TREE|COMMON_DIR|CONFIG[A-Z0-9_]*)=")
_GIT_LOCATION_OPTS = {"--git-dir", "--work-tree", "--config-env"}
_PREFIX_KEYWORDS = {"!", "{", "then", "do", "else", "elif", "if", "while", "until", "time"}
_REDIR_OP_RE = re.compile(r"\A[0-9]*(?:[<>]+[&|-]?|&>>?)\Z")
_HEREDOC_RE = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z0-9_.-]+)\2")
# Characters that make a path only the shell can resolve (expansion, glob, brace expansion).
_PATH_SPECIAL = set("$`*?[]{}")
# A line ending in one of these operators continues on the next line.
_CONTINUED_RE = re.compile(r"(?:&&|\|\||\|&|\|)[ \t]*\Z")


def _split_script(text):
    """[(line, heredoc_body_or_None)] for each logical command line of `text`.

    Splits at newlines a shell treats as separators (outside quotes and `$(...)`/backquotes),
    drops `#` comments, and lifts each here-document body OUT of the command stream, returning
    it beside the line that opened it. A body is data unless that line feeds it to a shell, which
    `_classify_segment` decides. A line ending in `&&`, `||` or `|` continues onto the next one,
    exactly as the shell continues it, so an and-or chain is never cut at a newline."""
    text = _join_continuations(text or "")
    lines, current, pending, bodies = [], [], [], []
    quote = None
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if quote:
            current.append(c)
            if quote == '"' and c == "\\" and i + 1 < n:
                current.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
        elif c == "\\" and i + 1 < n:
            current.append(c)
            current.append(text[i + 1])
            i += 2
            continue
        elif c == "`" or text.startswith("$(", i):
            end = _substitution_end(text, i)  # a newline inside it separates nothing outside
            current.append(text[i:end])
            i = end
            continue
        elif c == "#" and (not current or current[-1] in " \t;&|("):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c == "<" and text.startswith("<<", i) and not text.startswith("<<<", i):
            m = _HEREDOC_RE.match(text, i)
            if m:
                pending.append((m.group(1) == "-", m.group(3)))
                current.append(text[i:m.end()])
                i = m.end()
                continue
        elif c == "\n":
            i += 1
            for strip_tabs, word in pending:
                collected = []
                while i < n:
                    j = text.find("\n", i)
                    raw = text[i:] if j < 0 else text[i:j]
                    i = n if j < 0 else j + 1
                    if (raw.lstrip("\t") if strip_tabs else raw) == word:
                        break
                    collected.append(raw)
                bodies.append("\n".join(collected))
            pending = []
            if _CONTINUED_RE.search("".join(current)):
                current.append(" ")
                continue
            lines.append(("".join(current), "\n".join(bodies) if bodies else None))
            current, bodies = [], []
            continue
        current.append(c)
        i += 1
    if quote:
        raise ValueError("unterminated quote")
    lines.append(("".join(current), "\n".join(bodies) if bodies else None))
    return lines


def _substitution_end(line, i):
    """Index just past the `$(...)`, `${...}` or backquoted text that starts at `i`.
    Raises ValueError when it is not closed."""
    if line[i] == "`":
        close = line.find("`", i + 1)
        if close < 0:
            raise ValueError("unterminated substitution")
        return close + 1
    opener = line[i + 1]
    closer = ")" if opener == "(" else "}"
    depth, j, quote = 0, i + 1, None
    while j < len(line):
        c = line[j]
        if quote:
            if c == "\\" and quote == '"':
                j += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c == "\\":
            j += 2
            continue
        elif c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unterminated substitution")


def _substitutions(line):
    """The inner text of every `$(...)` and backtick substitution a shell would EXECUTE in `line`
    (not inside single quotes; `$((` arithmetic excluded)."""
    found = []
    i, n = 0, len(line)
    in_double = False
    while i < n:
        c = line[i]
        if c == "'" and not in_double:
            close = line.find("'", i + 1)
            i = n if close < 0 else close + 1
            continue
        if c == '"':
            in_double = not in_double
        elif c == "\\":
            i += 2
            continue
        elif c == "`" or (line.startswith("$(", i) and not line.startswith("$((", i)):
            end = _substitution_end(line, i)
            found.append(line[i + 1:end - 1] if c == "`" else line[i + 2:end - 1])
            i = end
            continue
        i += 1
    return found


def _shell_tokens(line):
    """[(text, is_operator)] for one command line, with quotes removed as a shell removes them.

    shlex cannot do this job: with punctuation_chars it returns a quoted `)` and a real one as
    the same token, and it glues adjacent operators (`);`, `))`) into one, which hid
    `(cd /x); git commit` from the segment splitter. Here an operator is reported only when it
    is unquoted, and `2>` is one redirection. `$(...)`, `${...}` and backquoted text stay inside
    their word; `_substitutions` examines what they run. Raises ValueError on an unterminated
    quote or substitution."""
    tokens, word, in_word = [], [], False
    i, n = 0, len(line)
    while i < n:
        c = line[i]
        if c in " \t\r\n":
            if in_word:
                tokens.append(("".join(word), False))
                word, in_word = [], False
            i += 1
            continue
        if c == "'":
            close = line.find("'", i + 1)
            if close < 0:
                raise ValueError("unterminated quote")
            word.append(line[i + 1:close])
            in_word, i = True, close + 1
            continue
        if c == '"':
            i += 1
            while i < n and line[i] != '"':
                if line[i] == "`" or line.startswith("$(", i):
                    end = _substitution_end(line, i)
                    word.append(line[i:end])
                    i = end
                    continue
                if line[i] == "\\" and i + 1 < n and line[i + 1] in '"\\$`':
                    i += 1
                word.append(line[i])
                i += 1
            if i >= n:
                raise ValueError("unterminated quote")
            in_word, i = True, i + 1
            continue
        if c == "\\":
            word.append(line[i + 1:i + 2])
            in_word, i = True, i + 2
            continue
        if c == "`" or line.startswith(("$(", "${"), i):
            end = _substitution_end(line, i)
            word.append(line[i:end])
            in_word, i = True, end
            continue
        op = next((o for o in _OPERATORS if line.startswith(o, i)), None)
        if op is None:
            word.append(c)
            in_word, i = True, i + 1
            continue
        text = "".join(word)
        io_number = text if (in_word and text.isdigit() and op[0] in "<>") else ""
        if in_word and not io_number:
            tokens.append((text, False))
        word, in_word = [], False
        tokens.append((io_number + op, True))
        i += len(op)
    if in_word:
        tokens.append(("".join(word), False))
    return tokens


def _segments(tokens):
    """Yield (item, op) per simple command: `item` is its word list (redirections included) and
    `op` the operator that ended it, None at the end of the line. An unquoted `(` or `)` is
    yielded as the item itself, so the caller can scope a `cd` to its subshell."""
    words = []
    for text, is_op in tokens:
        if not is_op or (text not in _SEGMENT_OPS and text not in ("(", ")")):
            words.append(text)
            continue
        if text in ("(", ")"):
            if words:
                yield words, None
            words = []
            yield text, None
            continue
        yield words, text
        words = []
    if words:
        yield words, None


def _strip_prefixes(words, here, home):
    """Drop what precedes the real command word: assignments, keywords and redirections.
    Wrappers are added in Task 7. Returns (words, the directory the command itself acts on):
    a `GIT_DIR=`-style assignment makes that unknowable. `home` is unused until Task 7."""
    k = 0
    while k < len(words):
        w = words[k]
        if _ASSIGNMENT_RE.match(w) or w in _PREFIX_KEYWORDS:
            if _GIT_ENV_RE.match(w):
                here = None
            k += 1
        elif _REDIR_OP_RE.match(w):
            k += 2
        else:
            break
    return words[k:], here


def _join_target(here, value, home):
    """`value` resolved against `here` as a shell and git would, or None when only the shell can
    resolve it: an expansion, a glob, brace expansion, a directory-stack tilde (`~+`, `~-`,
    `~1`), a `~` while `home` is None (see `commit_intent`), or a relative path against an
    unknown directory. `~user` comes from the password database, not HOME, so it resolves."""
    if set(value) & _PATH_SPECIAL:
        return None
    if value.startswith("~"):
        prefix = value[1:].split("/", 1)[0]
        if _DIRECTORY_STACK_TILDE.match(prefix) or (not prefix and home is None):
            return None
        value = os.path.expanduser(value) if prefix else home + value[1:]
    if here is None and not os.path.isabs(value):
        return None
    return os.path.normpath(os.path.join(here or os.sep, value))


def _cd(here, args, home, pushd=False):
    """The directory after `cd`/`pushd` with `args`, or None when the text cannot say. A form
    the shell rejects (an unknown option, more than one operand) fails without moving, and zsh
    reads two operands as a substitution, so neither can be treated as a move. A bare `cd` goes
    to `home`, which is None when HOME may have changed."""
    args = list(args)
    if pushd and (not args or args[0][:1] in "+-"):
        return None  # swaps or rotates the directory stack, or `-n` does not move at all
    while args and args[0].startswith("-") and args[0] != "-":
        opt = args.pop(0)
        if opt == "--":
            break
        if not set(opt[1:]) <= set("LPe@"):
            return None
    if len(args) > 1:
        return None
    if not args:
        return home
    target = args[0]
    if target == "-" or (os.environ.get("CDPATH") and not target.startswith(("/", ".", "~"))):
        return None
    return _join_target(here, target, home)


def _config_key(key):
    """A config key as git compares it: section and variable case-insensitive, subsection not."""
    head, dot, rest = key.partition(".")
    middle, _, last = rest.rpartition(".")
    return head.lower() + dot + (middle + "." if middle else "") + last.lower()


def _subcommand_intent(sub, rest, target, overrides):
    """Classify one resolved git subcommand. Task 8 adds the other commit producers."""
    if sub == "commit":
        return CommitIntent("commit", target, None)
    return _NO_INTENT


def _classify_git(args, here, depth, home, overrides=None):
    target = here
    overrides = dict(overrides or {})
    k = 0
    while k < len(args) and args[k].startswith("-"):
        t = args[k]
        name = t.split("=", 1)[0]
        if t == "-C" and k + 1 < len(args):
            target = _join_target(target, args[k + 1], home)
            k += 2
            continue
        if t == "-c" and k + 1 < len(args):
            key, eq, value = args[k + 1].partition("=")
            overrides[_config_key(key)] = value if eq else "true"
            k += 2
            continue
        if name in _GIT_LOCATION_OPTS:
            target = None  # another repository, or configuration taken from the environment
        k += 2 if (name in _GIT_GLOBAL_VALUE | _GIT_LOCATION_OPTS and "=" not in t) else 1
    if k >= len(args):
        return _NO_INTENT
    return _subcommand_intent(args[k], args[k + 1:], target, overrides)


def _classify_segment(words, here, body, depth, home):
    """(CommitIntent, directory after this segment) for one simple command. Only `cd`, `pushd`
    and `popd` move the directory; a prefix such as `GIT_DIR=` applies to its own command. A
    `HOME=` prefix changes what a bare `cd` or `cd ~` reaches, so `~` is not resolved."""
    if any(w.startswith("HOME=") for w in words):
        home = None
    words, inner = _strip_prefixes(words, here, home)
    if not words:
        return _NO_INTENT, here
    head = words[0]
    if head in ("cd", "pushd"):
        return _NO_INTENT, _cd(inner, words[1:], home, pushd=head == "pushd")
    if head == "popd":
        return _NO_INTENT, None
    if os.path.basename(head) == "git":
        return _classify_git(words[1:], inner, depth, home), here
    return _NO_INTENT, here


def _settle(here, start, late):
    """The directory after an and-or list that began in `start`. A cd that ran unconditionally
    (first in the list) into a directory this process can search is certain; any other change
    is not. `isdir` alone is not enough: `cd` fails on a directory without search permission."""
    if here == start:
        return here
    if late or here is None or not (os.path.isdir(here) and os.access(here, os.X_OK)):
        return None
    return here


def _regex_fallback(text, target):
    if _COMMIT_RE.search(text or ""):
        return CommitIntent("commit", target, None)
    return _NO_INTENT


def commit_intent(command, cwd, _depth=0):
    """The first commit-producing git invocation `command` would run, as a CommitIntent.

    Looks only at real command positions, so text inside quoted arguments, comments and data
    here-documents never counts, while `$(...)`/backtick substitutions (which a shell executes)
    do. Tracks `cd`, so the target is the directory the commit would act on -- or None when the
    shell may not have changed directory as written (see `_settle` and the subshell handling
    below). Any lexing failure falls back to `_COMMIT_RE` over the raw text -- the conservative
    old detector.

    HOME is trusted (for `~` and a bare `cd`) only in the FIRST command of a top-level call:
    nothing has run before it that could change HOME, which a later command cannot rely on
    (`export HOME=…`, `declare "HO""ME=…"`, a nested shell started with another HOME, ...).
    Elsewhere a `~` target is unknowable. The gate's own plain `git -C ~/repo commit` is always
    a first command, so it keeps working."""
    here = str(cwd) if cwd is not None else None
    ran = False
    if _depth > _MAX_DEPTH:
        return CommitIntent("other", here, "the command nests shells too deeply for the gate "
                                           "to follow; run git directly")
    try:
        lines = _split_script(command)
    except ValueError:
        return _regex_fallback(command, here)
    stack, compound = [], 0
    for line, body in lines:
        try:
            inners = _substitutions(line)
            tokens = _shell_tokens(line)
        except ValueError:
            found = _regex_fallback(line, here)
            if found.kind:
                return found
            continue
        for inner in inners:
            found = commit_intent(inner, here, _depth + 1)
            if found.kind:
                return found
        # `start`: the directory the current and-or list began in; `pure`: every operator so far
        # in it is `&&`; `late`: a cd in it ran only after another command succeeded.
        start, pure, late, prev = here, True, False, None
        for item, op in _segments(tokens):
            if item == "(":  # a subshell: nothing inside can move the outer directory
                stack.append((here, start, pure, late))
                start, pure, late, prev = here, True, False, None
                continue
            if item == ")":
                if stack:
                    here, start, pure, late = stack.pop()
                continue
            if prev == "||":
                pure = False
            first = item[0] if item else None
            if first in _COMPOUND_OPEN:
                compound += 1
            if item:
                # Not expanduser: with HOME unset it falls back to the passwd home, while a bare
                # `cd` in bash fails and stays put. Unset or empty HOME is unknown.
                home = (os.environ.get("HOME") or None) if (_depth == 0 and not ran) else None
                ran = True
                found, after = _classify_segment(item, here, body, _depth, home)
                if found.kind:
                    return found
                if after != here and not (prev in _PIPES or op in _PIPES or op == "&"):
                    # A pipeline element or background job runs in a subshell: no change.
                    if compound or op == "||" or not (prev in _LIST_ENDS or pure):
                        after = None
                    late = late or prev not in _LIST_ENDS
                    here = after
            if first in _COMPOUND_CLOSE:
                compound = max(0, compound - 1)
            if op in _LIST_ENDS:
                here = start if op == "&" else _settle(here, start, late)
                start, pure, late = here, True, False
            prev = op
        here = _settle(here, start, late)  # a newline ends the list
    return _NO_INTENT
```

`_shell_tokens` replaces `_posix_tokens` for this detector only; `parse_commit_request` keeps
`_posix_tokens`, because it refuses every shape but one plain `git commit` anyway.

Export `CommitIntent` and `commit_intent` in `reviewer_lib.py` (next to `is_git_commit`), then run
`python3 tools/gen_facade_contract.py`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_commit_intent.py tests/test_facade_contract.py -q -p no:cacheprovider && python3 tools/check_split_layering.py`
Expected: PASS; layering `PASS`.

- [ ] **Step 5: Commit green**

```bash
git add reviewer_git.py reviewer_lib.py tests/test_facade_contract.py
```
```bash
git commit -m "feat: detect commits by shell command position, ignoring quoted text, comments and data heredocs"
```

### Task 7: Unwrap wrappers, shells, `eval`, aliases and inline interpreters

**Files:**
- Modify: `reviewer_git.py` (`_strip_prefixes`, `_classify_segment`, `_classify_git`, new helpers)
- Test: `tests/test_commit_intent.py`

**Interfaces:**
- Consumes: `commit_intent`, `_classify_git`, `_classify_segment` (Task 6).
- Produces: `_repo_alias(target, name) -> str | None`. It runs
  `git -C <target> config --get alias.<name>` with a 5 s timeout and returns None on any failure.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_commit_intent.py
@pytest.mark.parametrize("command", [
    "/usr/bin/git commit -m x",
    '"git" commit -m x',
    "'git' commit -m x",
    "\\git commit -m x",
    "env git commit -m x",
    "env -i PATH=/usr/bin git commit -m x",
    "command git commit -m x",
    "nohup git commit -m x",
    "timeout 30 git commit -m x",
    "echo x | xargs git commit -m",
    "bash -c 'git commit -m x'",
    "sh -c \"cd sub && git commit -m x\"",
    "bash -lc 'git commit -m x'",
    'eval "git commit -m x"',
    "bash <<'EOF'\ngit commit -m x\nEOF",
    "git -c alias.ci=commit ci -m x",
    "git -c 'alias.sh=!git commit -m x' sh",
])
def test_wrapped_and_aliased_commits_are_found(command):
    assert _kind(command) is not None, command


@pytest.mark.parametrize("command", [
    "python3 -c \"import subprocess; subprocess.run(['git','commit','-m','x'])\"",
    "node -e \"require('child_process').execSync('git commit -m x')\"",
    "python3 - <<'EOF'\nimport os; os.system('git commit -m x')\nEOF",
    "g=git; $g commit -m x",
])
def test_opaque_commit_runners_are_refused(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "other" and intent.reason


def test_a_repository_alias_is_resolved(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "alias.ci", "commit"], check=True)
    assert rl.commit_intent("git ci -m x", str(tmp_path)).kind == "commit"


def test_a_wrapper_prefix_scopes_to_its_own_command():
    assert rl.commit_intent("env GIT_WORK_TREE=/other git commit -m x", CWD).target is None
    assert rl.commit_intent("env -C /other git commit -m x", CWD).target == "/other"
    assert rl.commit_intent("env -C /other true; git commit -m x", CWD).target == CWD


@pytest.mark.parametrize("command", [
    "env --chdir=/active git commit -m x",
    "env --chdir /active git commit -m x",
    "env -iC /active git commit -m x",
    "env -C/active git commit -m x",
    "sudo -D /active git commit -m x",
    "sudo --chdir=/active git commit -m x",
])
def test_every_spelling_of_a_wrapper_directory_option_moves_the_target(command):
    # From an inactive cwd, each of these commits in /active.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target == "/active", command


def test_sudo_login_runs_from_a_home_directory_the_text_does_not_name():
    assert rl.commit_intent("sudo -i git commit -m x", CWD).target is None


@pytest.mark.parametrize("command", [
    "env -S 'git commit -m x'",
    "env --split-string='git commit -m x'",
    "env -iS 'git commit -m x'",
    "env -S 'GIT_DIR=/o/.git git commit -m x'",
])
def test_env_split_string_is_parsed_as_the_command_it_runs(command):
    # GNU env -S runs its value as a command line.
    assert _kind(command) == "commit", command


def test_an_eval_that_changes_directory_makes_the_target_unknowable():
    assert rl.commit_intent('eval "cd /other"; git commit -m x', CWD).target is None
    assert rl.commit_intent('eval "$(ssh-agent -s)"; git commit -m x', CWD).target == CWD


def test_a_shell_reading_a_script_file_is_a_documented_gap():
    assert _kind("bash ./release.sh") is None  # README "What the gate cannot see"
```

- [ ] **Step 2: Run them to confirm red, then commit red**

Run: `python3 -m pytest tests/test_commit_intent.py -q -p no:cacheprovider`
Expected: the new cases FAIL.

```bash
git add tests/test_commit_intent.py
```
```bash
git commit -m "test: pin detection of wrapped, shell-nested, aliased and scripted commits"
```

- [ ] **Step 3: Implement**

Add these constants beside the Task 6 ones:

```python
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
_INTERPRETER_RE = re.compile(r"\A(?:python[0-9.]*|node|perl|ruby)\Z")
_INLINE_GIT_COMMIT_RE = re.compile(
    r"""(['"])git\1\s*,\s*(['"])commit\2|\bgit\s+(?:-\S+\s+)*commit\b""")
# wrapper -> (options that take a value, options whose value is the directory the command runs
#             in, options whose value is a command line the wrapper splits and runs)
_WRAPPERS = {
    "env": ({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
            {"-C", "--chdir"}, {"-S", "--split-string"}),
    "command": (set(), set(), set()), "builtin": (set(), set(), set()),
    "exec": ({"-a"}, set(), set()), "nohup": (set(), set(), set()),
    "nice": ({"-n", "--adjustment"}, set(), set()),
    "stdbuf": ({"-i", "-o", "-e", "--input", "--output", "--error"}, set(), set()),
    "sudo": ({"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t", "-T", "--user", "--group",
              "--close-from", "--chdir", "--host", "--prompt", "--other-user", "--role", "--type",
              "--command-timeout"}, {"-D", "--chdir"}, set()),
    "doas": ({"-u", "-C"}, set(), set()),
    "timeout": ({"-s", "--signal", "-k", "--kill-after"}, set(), set()),
    "xargs": ({"-I", "-n", "-L", "-d", "-E", "-P", "-s", "-a", "--arg-file", "--delimiter",
               "--max-args", "--max-procs", "--max-chars", "--max-lines", "--eof"}, set(), set()),
}
_UNRESOLVABLE_HEAD_REASON = (
    "the command name is a shell expansion the gate cannot resolve; call `git` by its name")
_INLINE_REASON = (
    "this inline script runs `git commit`, which the gate cannot review from outside; stage "
    "the change and run a plain `git commit -m ...` as its own tool call")
# Builtins cannot be shadowed by aliases, so an alias lookup is needed only for other words.
_GIT_BUILTINS = set(
    "add am apply archive bisect blame branch bundle cat-file check-attr check-ignore checkout "
    "cherry cherry-pick clean clone commit commit-graph commit-tree config count-objects "
    "describe diff diff-files diff-index diff-tree difftool fetch filter-branch for-each-ref "
    "format-patch fsck gc grep hash-object help init log ls-files ls-remote ls-tree merge "
    "merge-base mergetool mktag mktree mv name-rev notes pack-refs prune pull push range-diff "
    "read-tree rebase reflog remote repack replace rerere reset restore rev-list rev-parse "
    "revert rm shortlog show show-ref sparse-checkout stash status submodule switch "
    "symbolic-ref tag update-index update-ref var verify-commit verify-tag version worktree "
    "write-tree".split())
```

Replace `_strip_prefixes`, and add `_wrapper_option` beside it:

```python
def _wrapper_option(words, k, valued):
    """(option name, its value or None, index after it) for the wrapper option at words[k],
    in every spelling getopt accepts: `--opt value`, `--opt=value`, `-o value`, `-ovalue`, and a
    value-taking letter inside a cluster (`-iC dir`)."""
    opt = words[k]
    following = words[k + 1] if k + 1 < len(words) else ""
    if opt.startswith("--"):
        name, eq, value = opt.partition("=")
        if eq:
            return name, value, k + 1
        return (name, following, k + 2) if name in valued else (name, None, k + 1)
    for j in range(1, len(opt)):
        name = "-" + opt[j]
        if name in valued:
            return (name, opt[j + 1:], k + 1) if j + 1 < len(opt) else (name, following, k + 2)
    return opt, None, k + 1


def _strip_prefixes(words, here, home):
    """Drop what precedes the real command word: assignments, keywords, redirections and
    transparent wrappers (`env`, `command`, `nohup`, `timeout N`, `xargs`, `sudo`, ...).
    Returns (words, the directory the command acts on), which applies to this command only:
    - a wrapper's directory option (`env -C`/`--chdir`, `sudo -D`/`--chdir`) moves it;
    - `sudo -i`/`--login` runs it from the target user's home, and a `GIT_DIR=`-style assignment,
      bare or after `env`, points git elsewhere: both make it unknowable;
    - `env -S`/`--split-string` runs its value as a command line, so that value is split into
      words and parsed in place, like the rest of the command."""
    k = 0
    while k < len(words):
        w = words[k]
        name = os.path.basename(w)
        if _ASSIGNMENT_RE.match(w) or w in _PREFIX_KEYWORDS:
            if _GIT_ENV_RE.match(w):
                here = None
            k += 1
        elif _REDIR_OP_RE.match(w):
            k += 2
        elif name in _WRAPPERS:
            valued, directory, split = _WRAPPERS[name]
            k += 1
            while k < len(words) and (words[k].startswith("-") or
                                      (name == "env" and _ASSIGNMENT_RE.match(words[k]))):
                opt = words[k]
                if opt == "--":
                    k += 1
                    break
                if not opt.startswith("-"):  # env NAME=value
                    if _GIT_ENV_RE.match(opt):
                        here = None
                    k += 1
                    continue
                if name == "sudo" and (opt == "--login" or
                                       (not opt.startswith("--") and "i" in opt[1:])):
                    here = None
                option, value, k = _wrapper_option(words, k, valued)
                if option in directory:
                    here = _join_target(here, value, home)
                if option in split:
                    try:
                        inserted = shlex.split(value)
                    except ValueError:
                        inserted = value.split()
                    words = words[:k] + inserted + words[k:]
            if name == "timeout" and k < len(words):
                k += 1  # the DURATION operand
        else:
            break
    return words[k:], here
```

Replace `_classify_segment` (keeping `cd`, adding shells, `eval`, interpreters and unresolvable
heads):

```python
def _classify_segment(words, here, body, depth, home):
    """(CommitIntent, directory after this segment) for one simple command. Only `cd`, `pushd`,
    `popd` and an `eval` that runs one of them move the directory; a prefix such as `env -C` or
    `GIT_DIR=` applies to its own command, and a nested shell cannot move its parent. A `HOME=`
    prefix changes what a bare `cd` or `cd ~` reaches, so `~` is not resolved."""
    if any(w.startswith("HOME=") for w in words):
        home = None
    words, inner = _strip_prefixes(words, here, home)
    if not words:
        return _NO_INTENT, here
    head = words[0]
    name = os.path.basename(head)
    if head in ("cd", "pushd"):
        return _NO_INTENT, _cd(inner, words[1:], home, pushd=head == "pushd")
    if head == "popd":
        return _NO_INTENT, None
    if _UNRESOLVABLE_C_RE.search(head):
        if any(w in ("commit", "commit-tree", "merge", "cherry-pick", "revert", "am", "pull")
               for w in words[1:4]):
            return CommitIntent("other", inner, _UNRESOLVABLE_HEAD_REASON), here
        return _NO_INTENT, here
    here, outer = inner, here  # everything below acts on `inner` and leaves `outer` in place
    if name == "git":
        return _classify_git(words[1:], here, depth, home), outer
    if name == "eval":
        script = " ".join(words[1:])
        moves = re.search(r"(?:\A|[;&|(\s])(?:cd|pushd|popd)\b", script)
        return commit_intent(script, here, depth + 1), (None if moves else outer)
    if name in _SHELLS:
        rest = words[1:]
        index = 0
        while index < len(rest):
            word = rest[index]
            if _REDIR_OP_RE.match(word):  # `bash <<'EOF'`: `<<` and its word are not operands
                index += 2
                continue
            if word.startswith("-") and not word.startswith("--") and "c" in word[1:]:
                script = rest[index + 1] if index + 1 < len(rest) else ""
                return commit_intent(script, here, depth + 1), outer
            if not word.startswith("-"):
                return _NO_INTENT, outer  # a script FILE: a documented gap
            index += 1
        if body is not None:  # `bash <<EOF` / `sh -s <<EOF`: the body IS the script
            return commit_intent(body, here, depth + 1), outer
        return _NO_INTENT, outer
    if _INTERPRETER_RE.match(name):
        code = ""
        for index, word in enumerate(words[1:], 1):
            if word in ("-c", "-e", "--eval") and index + 1 < len(words):
                code = words[index + 1]
                break
        if not code and body is not None and "-" in words[1:]:
            code = body
        if code and _INLINE_GIT_COMMIT_RE.search(code):
            return CommitIntent("other", here, _INLINE_REASON), outer
    return _NO_INTENT, outer
```

Add alias resolution at the end of `_classify_git` (replacing its final `return`). An alias
given with `-c alias.<name>=…` is in `overrides` under `alias.<name lowercased>`, the key git
looks up:

```python
    sub, rest = args[k], args[k + 1:]
    if sub not in _GIT_BUILTINS:
        expansion = overrides.get("alias." + sub.lower())
        if expansion is None and target is not None:
            expansion = _repo_alias(target, sub)
        if expansion:
            if expansion.startswith("!"):
                script = expansion[1:] + "".join(" " + shlex.quote(a) for a in rest)
                return commit_intent(script, target, depth + 1)
            try:
                words = shlex.split(expansion)
            except ValueError:
                words = []
            if words and depth < _MAX_DEPTH:
                return _classify_git(words + rest, target, depth + 1, home, overrides)
    return _subcommand_intent(sub, rest, target, overrides)


def _repo_alias(target, name):
    """`alias.<name>` as git itself resolves it (includes and all config scopes), or None."""
    try:
        p = subprocess.run(["git", "-C", str(target), "config", "--get", "alias." + name],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout.strip() or None if p.returncode == 0 else None
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/test_commit_intent.py -q -p no:cacheprovider && python3 tools/check_split_layering.py`
Expected: PASS.

- [ ] **Step 5: Commit green**

```bash
git add reviewer_git.py
```
```bash
git commit -m "feat: see through wrappers, nested shells, eval, git aliases and inline interpreters when detecting commits"
```

### Task 8: Other commit producers, hook wiring, and the documented residual

**Files:**
- Modify: `reviewer_git.py` (`_subcommand_intent`; `is_git_commit`; a `parse_commit_request`
  message for a non-plain `git` spelling)
- Modify: `commit_hook.py:59-94` (detection and target via `commit_intent`)
- Modify: `tests/test_reviewer_lib.py:30-58` (`git commit-tree abc123` flips from negative to
  detected, deliberately)
- Modify: `README.md` (new subsection "What the gate cannot see")
- Test: `tests/test_commit_intent.py`, `tests/test_gate_owns_commit.py`

**Interfaces:**
- Consumes: `commit_intent` (Tasks 6-7).
- Produces: `is_git_commit(command) -> bool`, now `commit_intent(command, ".").kind is not None`.

- [ ] **Step 1: Write the failing tests**

```python
# appended to tests/test_commit_intent.py
@pytest.mark.parametrize("command,needle", [
    ("git commit-tree abc -m x", "commit-tree"),
    ("git merge feature", "--no-commit"),
    ("git merge --no-ff feature", "--no-commit"),
    ("git merge --continue", "--no-commit"),
    ("git cherry-pick abc123", "cherry-pick -n"),
    ("git revert HEAD", "--no-commit"),
    ("git am 0001.patch", "git apply --index"),
    ("git pull", "--ff-only"),
    ("git pull --rebase", "--ff-only"),
    # Git resolves these options last-wins, so a later switch re-enables the commit:
    ("git merge --no-commit --commit feature", "--no-commit"),
    ("git merge --no-commit --comm feature", "--no-commit"),  # an abbreviation of --commit
    ("git merge --squash --no-squash feature", "--no-commit"),
    ("git merge --ff-only --no-ff feature", "--no-commit"),
    ("git merge --ff-only --ff feature", "--no-commit"),
    ("git cherry-pick -n --commit abc123", "cherry-pick -n"),  # --commit negates --no-commit
    ("git revert --no-commit --commit HEAD", "--no-commit"),
    ("git cherry-pick --skip", "cherry-pick -n"),  # resumes the sequence, committing the rest
])
def test_other_commit_producers_are_refused_with_the_no_commit_route(command, needle):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "other" and needle in intent.reason, command


@pytest.mark.parametrize("command", [
    "git merge --no-commit --no-ff feature", "git merge --squash feature",
    "git merge --ff-only origin/main", "git merge --abort",
    "git merge --commit --no-commit feature", "git merge --no-commit --ff feature",
    "git merge --squash --commit feature",  # git refuses the combination; nothing is committed
    "git cherry-pick -n abc123", "git cherry-pick --no-commit abc", "git cherry-pick -xn abc",
    "git revert --no-commit HEAD", "git revert -n HEAD", "git am --abort",
    "git merge-base a b", "git log --merges",
])
def test_no_commit_modes_are_allowed(command):
    assert rl.commit_intent(command, CWD).kind is None, command


@pytest.fixture
def pull_repo(tmp_path, monkeypatch):
    """A repository on branch `main` whose pull mode comes only from its own config."""
    from repo_context import init_repo
    for name in ("HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path))  # no global pull.rebase from the host
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    repo = tmp_path / "repo"
    init_repo(repo)
    return str(repo)


@pytest.mark.parametrize("command", [
    "git pull --ff-only", "git pull --no-commit", "git pull --squash",
    "git pull --rebase --no-rebase --no-commit", "git pull --rebase=false --squash",
])
def test_no_commit_pull_modes_are_allowed(pull_repo, command):
    assert rl.commit_intent(command, pull_repo).kind is None, command


@pytest.mark.parametrize("command", [
    "git pull --no-commit --commit", "git pull --squash --no-squash", "git pull --ff-only --ff",
    "git pull --no-commit --rebase", "git pull -r --no-commit", "git pull --ff-only --rebase",
    "git -c pull.rebase=true pull --no-commit",
    "git -c branch.main.rebase=merges pull --squash",
])
def test_pull_that_rebases_or_re_enables_the_commit_is_refused(pull_repo, command):
    intent = rl.commit_intent(command, pull_repo)
    assert intent.kind == "other" and "--ff-only" in intent.reason, command


@pytest.mark.parametrize("key", ["pull.rebase", "branch.main.rebase"])
def test_configured_rebase_makes_a_no_commit_pull_commit(pull_repo, key):
    # Verified: under pull.rebase=true, `git pull --no-commit` rebases and moves HEAD.
    import subprocess
    subprocess.run(["git", "-C", pull_repo, "config", key, "true"], check=True)
    assert rl.commit_intent("git pull --no-commit", pull_repo).kind == "other"
    assert rl.commit_intent("git pull --no-rebase --no-commit", pull_repo).kind is None
```

```python
# appended to tests/test_gate_owns_commit.py
def test_a_nested_commit_in_an_active_repo_is_refused_before_review(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    called = []
    _approve(monkeypatch, record=called)
    before = _git(repo, "rev-parse", "HEAD")
    for command in ("bash -c 'git commit -m x'", "/usr/bin/git commit -m x",
                    "git cherry-pick HEAD"):
        out = _run(repo, command, capsys, monkeypatch)
        assert out["hookSpecificOutput"]["permissionDecision"] == "deny", command
    assert called == [] and _git(repo, "rev-parse", "HEAD") == before


def test_an_inactive_repo_is_never_touched(tmp_path, monkeypatch, capsys):
    # Review Focus 1.
    repo = _claude_repo(tmp_path)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))  # gate NOT enabled here
    for command in ("git merge feature", "bash -c 'git commit -m x'", "git cherry-pick HEAD"):
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
            {"cwd": repo, "session_id": "s", "tool_name": "Bash",
             "tool_input": {"command": command}})))
        assert commit_hook.main() == 0
        assert capsys.readouterr().out.strip() == "", command


def test_a_heredoc_mentioning_a_commit_runs_in_an_active_repo(tmp_path, monkeypatch, capsys):
    # Review Focus 2, end to end.
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"cwd": repo, "session_id": "s", "tool_name": "Bash",
         "tool_input": {"command": "cat > notes.md <<'EOF'\nthen git commit -m x\nEOF"}})))
    assert commit_hook.main() == 0
    assert capsys.readouterr().out.strip() == ""


def test_cd_into_an_active_repo_from_elsewhere_is_gated(tmp_path, monkeypatch, capsys):
    repo = _claude_repo(tmp_path)
    _activate(repo, monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"cwd": str(elsewhere), "session_id": "s", "tool_name": "Bash",
         "tool_input": {"command": "cd {} && git commit -m x".format(repo)}})))
    assert commit_hook.main() == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"][
        "permissionDecision"] == "deny"
```

In `tests/test_reviewer_lib.py:45`, move `git commit-tree abc123` from the negative list to the
positive list, with the comment `# #15: commit-tree writes a commit the reviewer never sees`.

- [ ] **Step 2: Run them to confirm red, then commit red**

Run: `python3 -m pytest tests/test_commit_intent.py tests/test_gate_owns_commit.py tests/test_reviewer_lib.py -q -p no:cacheprovider`
Expected: the new cases FAIL.

```bash
git add tests/test_commit_intent.py tests/test_gate_owns_commit.py tests/test_reviewer_lib.py
```
```bash
git commit -m "test: pin refusal of merge/cherry-pick/revert/am/pull/commit-tree and hook wiring of commit intent"
```

- [ ] **Step 3: Implement the subcommand policy**

Replace `_subcommand_intent`:

```python
# subcommand -> the no-commit route to offer when it would commit
_COMMIT_PRODUCERS = {
    "commit-tree": "`git commit-tree` writes a commit object the reviewer never sees and has no "
                   "no-commit form; stage the change and run a plain `git commit -m ...`",
    "merge": "`git merge` creates a merge commit the reviewer never sees. Run `git merge "
             "--no-commit --no-ff <branch>` (or `--squash`, or `--ff-only`), resolve, then a "
             "plain `git commit -m ...`",
    "cherry-pick": "`git cherry-pick` commits without review. Run `git cherry-pick -n <commit>`, "
                   "then a plain `git commit -m ...`",
    "revert": "`git revert` commits without review. Run `git revert --no-commit <commit>`, then "
              "a plain `git commit -m ...`",
    "am": "`git am` commits without review. Run `git apply --index <patch>`, then a plain "
          "`git commit -m ...`",
    "pull": "`git pull` can create a merge commit or rebased commits the reviewer never sees. "
            "Run `git pull --ff-only`, `--no-commit` or `--squash` with rebasing off, or "
            "`git fetch` then `git merge --ff-only` (or `--no-commit`/`--squash` and a plain "
            "`git commit -m ...`)",
}
_FALSE_WORDS = {"false", "no", "off", "0", ""}


def _abbreviates(arg, option):
    """git accepts any unambiguous prefix of a long option. Matching a prefix as the option that
    RE-ENABLES a commit errs toward refusing; git itself rejects an ambiguous one."""
    return len(arg) > 2 and option.startswith(arg)


def _merge_commits(rest):
    """Whether `git merge`/`git pull` with these arguments may create a commit. Options resolve
    last-wins, as git resolves them: `--commit` undoes `--no-commit`, `--no-squash` undoes
    `--squash`, `--ff`/`--no-ff` undo `--ff-only`. The no-commit switches count only when spelled
    in full; an abbreviation of one is refused, never guessed."""
    commit, squash, ff_only = True, False, False
    for arg in rest:
        if arg == "--":
            break
        if not arg.startswith("--"):
            continue  # merge has no short no-commit switch (`-n` is --no-stat)
        name = arg.split("=", 1)[0]
        if name in ("--abort", "--quit"):
            return False
        if name == "--no-commit":
            commit = False
        elif name == "--squash":
            squash = True
        elif name == "--ff-only":
            ff_only = True
        elif _abbreviates(name, "--continue"):
            return True
        elif _abbreviates(name, "--commit"):
            commit = True
        elif _abbreviates(name, "--no-squash"):
            squash = False
        elif _abbreviates(name, "--no-ff") or _abbreviates(name, "--ff"):
            ff_only = False
    return not (squash or not commit or ff_only)


def _picks_commit(rest):
    """Whether `git cherry-pick`/`git revert` with these arguments commits. `-n`/`--no-commit`
    (also inside a short cluster such as `-xn`) stops it, until a later `--commit` -- the
    negation git derives from `--no-commit` -- turns committing back on."""
    commit = True
    for arg in rest:
        if arg == "--":
            break
        name = arg.split("=", 1)[0]
        if name in ("--abort", "--quit"):
            return False
        if name == "--no-commit":
            commit = False
        elif _abbreviates(name, "--continue") or name == "--skip":
            return True  # both resume the sequencer, which commits the remaining picks
        elif _abbreviates(name, "--commit"):
            commit = True
        elif arg.startswith("-") and not arg.startswith("--") and "n" in arg[1:] \
                and arg[1:2] not in ("m", "S", "X"):  # value-taking short options
            commit = False
    return commit


def _config_value(target, overrides, key):
    """`key` as git would read it for a command in `target`: a `-c` override first, then the
    repository's configuration. Returns the value, None when unset, or False when it cannot be
    read."""
    if key in overrides:
        return overrides[key]
    if target is None:
        return False
    try:
        p = subprocess.run(["git", "-C", str(target), "config", "--get", key],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if p.returncode == 1:
        return None
    return p.stdout.strip() if p.returncode == 0 else False


def _pull_rebases(rest, target, overrides):
    """Whether `git pull` would rebase: the last `--rebase[=…]`/`-r`/`--no-rebase` on the command
    line, else `branch.<current>.rebase`, else `pull.rebase`. Anything it cannot read counts as
    rebasing, so the gate refuses rather than guesses."""
    rebase = None
    for arg in rest:
        if arg == "--":
            break
        name, eq, value = arg.partition("=")
        if name == "--no-rebase":
            rebase = False
        elif _abbreviates(name, "--rebase"):
            rebase = not (eq and value.lower() in _FALSE_WORDS)
        elif arg.startswith("-") and not arg.startswith("--") and "r" in arg[1:] \
                and arg[1:2] not in ("X", "s", "S", "j", "o"):  # value-taking short options
            rebase = True
    if rebase is not None:
        return rebase
    if target is None:
        return True
    try:
        p = subprocess.run(["git", "-C", str(target), "symbolic-ref", "--short", "-q", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        branch = p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return True
    keys = (["branch.{}.rebase".format(branch)] if branch else []) + ["pull.rebase"]
    for key in keys:
        value = _config_value(target, overrides, key)
        if value is False:
            return True
        if value is not None:
            return value.lower() not in _FALSE_WORDS
    return False


def _subcommand_intent(sub, rest, target, overrides):
    if sub == "commit":
        return CommitIntent("commit", target, None)
    if sub not in _COMMIT_PRODUCERS:
        return _NO_INTENT
    if sub == "commit-tree":
        commits = True
    elif sub == "merge":
        commits = _merge_commits(rest)
    elif sub == "pull":
        commits = _pull_rebases(rest, target, overrides) or _merge_commits(rest)
    elif sub in ("cherry-pick", "revert"):
        commits = _picks_commit(rest)
    else:  # am
        commits = not any(arg.split("=", 1)[0] in ("--abort", "--quit", "--show-current-patch")
                          for arg in rest)
    return CommitIntent("other", target, _COMMIT_PRODUCERS[sub]) if commits else _NO_INTENT
```

`branch.<name>.rebase` outranks `pull.rebase`, whatever scope each is set in, which is why the
lookup is per key and not "first scope that answers". A `-c` override of either key wins over
the repository's value for the same key.

Replace `is_git_commit`:

```python
def is_git_commit(command):
    """Back-compat predicate: does `command` run any commit-producing git (see commit_intent)?"""
    return commit_intent(command, ".").kind is not None
```

In `parse_commit_request`, before `if tokens[0] != "git":`, add:

```python
    if tokens[0] != "git" and os.path.basename(tokens[0]) == "git":
        return None, (
            "the gate runs `git` itself from PATH, so call it by its plain name "
            "(`git commit -m ...`), not `{}`".format(tokens[0])
        )
```

- [ ] **Step 4: Wire the hook**

Replace `commit_hook.py:63-94` (from the "Cheap precision check" comment through
`if not rl.is_active(cwd): return 0`) with:

```python
    command = (data.get("tool_input") or {}).get("command") or ""
    raw_cwd = data.get("cwd") or rl.project_dir()
    intent = rl.commit_intent(command, raw_cwd)
    if intent.kind is None:
        return 0
    # The target is a shell expansion (`git -C "$R"`, `cd "$R"`): the gate cannot know which
    # repository this acts on. Fail CLOSED while the reviewer is active for ANY project -- the
    # variable may point at one of them -- and stay out of the way when it is off everywhere.
    if intent.target is None:
        if rl.any_reviewer_active():
            reason = (
                "reviewerAgent cannot determine which repository this commit targets: its "
                "directory is a shell variable or command substitution the gate cannot resolve, "
                "so it cannot review the correct staged set. Re-run from the repository "
                "directory as a plain `git commit`, or pass a literal path "
                "(`git -C /absolute/path commit`)."
            )
            rl.log("[commit] key={} unresolvable target cmd={!r} -> denied (fail-closed)".format(
                rl.canonical_project_key(raw_cwd), rl.short(command, 120)))
            print(json.dumps(rl.deny_output(
                reason,
                "[reviewerAgent] git commit blocked: the target directory is a shell variable "
                "the gate cannot resolve. Use a literal path or commit from the repo directory.",
            )))
        return 0
    cwd = intent.target
    if not rl.is_active(cwd):  # resolves cwd -> canonical project root internally
        return 0
    if intent.kind == "other":
        rl.log("[commit] key={} commit-producing form refused ({}) cmd={!r}".format(
            rl.canonical_project_key(cwd), rl.short(intent.reason, 120), rl.short(command, 120)))
        print(json.dumps(rl.deny_output(
            "reviewerAgent reviews every commit in this repository, and this call would create "
            "one it cannot review: {}.".format(intent.reason),
            "[reviewerAgent] blocked before review: {}".format(rl.short(intent.reason, 200)),
        )))
        return 0
```

`git_c_effective_dir` and `commit_c_unresolvable` stay exported; their tests still pin them.

- [ ] **Step 5: Document the residual in README.md**

Add after the paragraph at `README.md:178-181`:

```markdown
#### What the gate cannot see

The gate reads the agent's Bash command before it runs. It follows real command positions,
`cd` (a `cd` in a subshell, pipeline or background job does not move a later commit, and a
`cd` it cannot be sure ran makes the target unknown, which it refuses while any project has the
reviewer on), `bash -c`/`sh -c`/`eval`, a heredoc fed to a shell, wrappers such as `env`,
`nohup`, `timeout` and `xargs`, `git -c alias.*` and repository aliases. It refuses a `merge`,
`cherry-pick`, `revert`, `am` or `pull` that would commit, judging the options the way git does
(the last of `--commit`/`--no-commit` wins, and a `pull` in rebase mode, whether from the command
line or from `pull.rebase`/`branch.<name>.rebase`, is refused). Each of those has a no-commit
route: `--no-commit`, `--squash`, `--ff-only`, `-n`, or `git apply --index` for `am`.
`commit-tree` has none, so it is refused outright; stage the change and make a plain
`git commit` instead. Inline `python -c`/`node -e` scripts that spell out `git commit` are
refused too. It cannot see:

- a script file (`./release.sh`, `make`, `npm run …`), or a sourced one (`source`/`.`), that
  commits or changes directory;
- a whole command held in a variable (`$CMD`), or git reached through a symlink with another name;
- an inline script that assembles the command at run time (joining `"git "` and `"commit"`);
- `git rebase`, which replays existing commits;
- plumbing (`hash-object`/`mktree`/`update-ref`) run from a script.

These are the limits of reading a command's text. Keep them in mind when writing
`reviewerAgent.md`.
```

- [ ] **Step 6: Run the gate suites**

Run: `python3 -m pytest tests/test_commit_intent.py tests/test_gate_owns_commit.py tests/test_commit_hook.py tests/test_commit_request.py tests/test_reviewer_lib.py tests/test_public_documents.py -q -p no:cacheprovider`
Expected: PASS. A `test_commit_hook.py` case that pinned the old `-C` unresolvable message wording
should still match. Its text changed only from "`git -C` path" to "directory"; update that
assertion if it pinned the exact phrase.

- [ ] **Step 7: Commit green**

```bash
git add reviewer_git.py commit_hook.py README.md
```
```bash
git commit -m "fix: gate every commit-producing git form the command text reveals and document what it cannot see"
```

---

## Phase C2 — Follow-ups from the Phase C run (added 2026-09-26)

Operator decisions after Phase C: fix the stop supervisor answering with the commit gate's word;
narrow the unresolved-command-word rule (option (a) below); stop refusing inline scripts that
mention "git commit" only as data (C2.3, a bounded fix); record red-phase commits as legitimate
in `reviewerAgent.md` (this repository's brief, not the plugin's prompts, because the plan -> red
-> green workflow is this project's convention, not something the plugin enforces everywhere).
Phase D stays skipped. For Phase F: do Tasks 14-17. Check Task 13 (#18) live after the reinstall,
and change it only if `/reviewerDirective` really fails here: the operator used it recently on
Linux, and it worked. Phase H runs in a local Docker container (Ubuntu 20.04, git 2.25), not on the
AWS host.

### Task C2.1: The stop supervisor is never framed as the commit auditor, and never relays `APPROVED`

Observed 2026-09-26 (reviewer.log, stop role session `01a0dc28…`): three consecutive stops were
blocked with the directive `APPROVED`. The supervisor's own transcript shows it ran `git diff
--cached` "before deciding whether this commit is safe to approve". Two causes:

1. `apply_context(role=ROLE_STOP)` prepends `_CAPSULE_UNIVERSAL`, whose text is the commit
   reviewer's job description ("find bugs … SUGGESTIONS, which never block a commit"). The operator
   messages it carries that turn quoted a whole `REJECTED` commit review, and the final message was
   a commit summary, so the supervisor answered as a commit auditor.
2. `stop_hook.py` treats every reply other than `STOP_OK` as a directive, so the gate's approval
   word was relayed as "NOT yet complete … Do the following: APPROVED".

**Files:**
- Modify: `reviewer_prompts.py` (`reviewer_capsule` takes `role`; new `_CAPSULE_STOP`; `apply_context` passes `role`)
- Modify: `reviewer_stopcontinue.py` (`stop_ok_position` returns `"gate"` for a commit-gate verdict)
- Modify: `stop_hook.py` (a `"gate"` reply allows the stop with a notice; it is not a directive and does not arm the watchdog)
- Test: `tests/test_stop_hook.py`, `tests/test_brief_composition.py`

**Behaviour:**
- The stop role's capsule says: this call is NOT a commit audit; there is no staged set, so do not
  run `git diff --cached` or review code for defects; quoted commit reviews in the operator messages
  are history, not a request; the only valid replies are `STOP_OK` or a directive; never `APPROVED`
  or `REJECTED`. It still names the brief's path, for project context only.
- The commit capsule (`commit_audit=True`) and the question-answerer capsule are unchanged.
- `stop_ok_position(out)` returns `"gate"` when the first non-empty line opens with the whole word
  `APPROVED` (the commit gate's approval sentinel). `is_stop_ok` stays `first`/`last` only.
- `stop_hook.py`: `keep_going` is False for `"gate"`. The stop is allowed (fail-open, like any
  reply that carries no usable stop verdict), no nudge is spent, the watchdog is NOT armed (only a
  real `STOP_OK` arms it), and a `systemMessage` says the supervisor answered with the commit gate's
  verdict instead of a stop verdict. The log line shows `stop_ok=gate`.
- A reply opening with `REJECTED` stays a directive: it carries content the agent can act on.

**Tests (red first):**
- `test_a_bare_commit_gate_approval_is_not_relayed_as_a_directive`: fake supervisor stdout
  `APPROVED`. Expect no `decision`, the notice in `systemMessage`, nudge count 0, and
  `stop_ok=gate` in the log.
- `test_a_commit_gate_approval_with_suggestions_is_not_relayed`: stdout `APPROVED\nSUGGESTIONS:\n1. x`.
  Same expectations.
- `test_the_stop_capsule_is_not_the_commit_reviewers`: `apply_context(p, repo, role=ROLE_STOP)`
  does not contain `"find bugs"` or `"never block a commit"`, and does contain `"NOT a commit
  audit"` and `"STOP_OK"`. `apply_context(p, repo)` (reviewer role) still contains
  `"REVIEWER STANDING REMINDER"`.

### Task C2.2: Refuse an unresolved command word only when the visible text names a commit producer

The Phase C rule refuses, in a gated repository, any command whose first word is an expansion the
call does not assign (`"$PYTHON" -m pytest`, `$HOME/bin/x`, `eval "$(ssh-agent -s)"`). The operator
rejected that as too broad. The gate guards against an agent skipping review, not against one
deliberately hiding a commit. A script file (`bash release.sh`) is already a documented blind spot,
so refusing every variable buys little and breaks everyday commands.

**New rule (option (a)):** an unresolved command word is refused only when a later word of the same
simple command is a commit-producing git subcommand (`_PRODUCER_WORDS`: `commit`, `commit-tree`,
`merge`, `cherry-pick`, `revert`, `am`, `pull`), e.g. `$GIT_BIN commit -m x`. Otherwise it has no
commit intent. `git $SUBCOMMAND` stays refused: `git` itself is visible and any subcommand may run.
`git -c "$X" <alias>` is unchanged.

**Files:**
- Modify: `reviewer_git.py` (`_classify_segment`, the `_UNRESOLVABLE_C_RE.search(head)` branch)
- Modify: `README.md` ("What the gate cannot see": drop "is refused while the repository is
  gated" for commands it cannot read; add a bullet: a commit held entirely in a variable or command
  output set outside the command, e.g. `$CMD` with `CMD='git commit -m x'` in the environment, or
  `eval "$(…)"`)
- Test: `tests/test_commit_intent.py`

**Tests (red first):**
- New `test_an_unresolved_command_word_without_a_commit_producer_is_not_refused`, parametrized:
  `'"$PYTHON" -m pytest -q'`, `"$HOME/bin/tool --flag"`, `'eval "$(ssh-agent -s)"'`,
  `'eval "$UNSET_ELSEWHERE"'`, `"$GIT_CMD -m x"`, `'bash -c "$(curl -s https://example.com/x)"'`.
  Each has `kind is None`.
- Move `'eval "$UNSET_ELSEWHERE"'` and the `curl` case out of
  `test_code_the_text_cannot_show_is_refused`, and `"$GIT_CMD -m x"` out of
  `test_a_command_word_the_call_cannot_pin_down_is_refused`. These cases now pin the documented
  residual.
- Unchanged and still refused: `$GIT_BIN commit -m x`, `$GIT_BIN --no-pager -C /tmp commit -m x`,
  `cmd=git; false && cmd=echo; $cmd commit -m x`, `git $SUBCOMMAND -m x`.
- `tests/test_commit_hook.py`: a gated repository allows `"$PYTHON" -m pytest` (no deny output).

### Task C2.3: Inline scripts that carry "git commit" only as data are not refused

`_INLINE_GIT_COMMIT_RE` scans the whole inline script, so a script that only *mentions* git and a
producer is refused: `python3 -c "print('run git commit after review')"`, or a Python heredoc that
writes a report containing "git commit". C2.2 does not cover this: the command word (`python3`) is
resolved.

**Bounded fix (not general interpreter analysis):** refuse an inline script only when it matches
`_INLINE_GIT_COMMIT_RE` AND it contains a process-spawning primitive for its interpreter:

| Interpreter | Spawning primitives (word match) |
|---|---|
| python | `subprocess`, `system`, `popen`, `exec[lv]p?e?`, `spawn[lv]?p?e?`, `posix_spawnp?`, `pty` |
| node | `child_process`, `exec`, `execSync`, `execFile`, `execFileSync`, `spawn`, `spawnSync`, `fork` |
| perl | `system`, `exec`, `qx`, `open`, or a backtick |
| ruby | `system`, `exec`, `spawn`, `popen\w*`, `Open3`, `capture\w*`, a backtick, or `%x` |

Backticks count only for perl and ruby, where they run a command. In Python they are text, and a
Markdown report quoting `` `git commit` `` is exactly the harmless case. The residual is accepted
and documented: a script that spawns something unrelated AND mentions "git commit" as text is still
refused. Tying the match to the spawn call's own argument would need real interpreter analysis,
which stays deferred.

**Files:**
- Modify: `reviewer_git.py` (new `_INLINE_SPAWN_RE` keyed by interpreter family; the
  `_INTERPRETER_RE` branch in `_classify_segment` requires both matches)
- Modify: `README.md` (the inline-script sentence says "that run a command and mention `git`
  and a commit-producing subcommand")
- Test: `tests/test_commit_intent.py`

**Tests (red first):**
- New `test_an_inline_script_that_only_mentions_a_commit_is_not_refused`, parametrized:
  `python3 -c "print('run git commit after review')"`, and
  `python3 - <<'EOF'\nopen('report.md', 'w').write('Next: `git commit -m x` once approved')\nEOF`.
  Each has `kind is None`.
- Unchanged and still refused (`test_opaque_commit_runners_are_refused`): `subprocess.run([...
  'commit' ...])`, `os.system('git commit -m x')` in a heredoc, and node `execSync('git commit
  -m x')`.

### Commit order

Each task gets its own red commit, then its own green commit, so no green commit leaves another
task's already-committed tests red.

1. This plan amendment with the `reviewerAgent.md` red-phase rule. The rule also covers the
   green commit that follows: its tests are the ones its red commit already landed.
2. C2.1 red, then C2.1 green.
3. C2.2 red, then C2.2 green with its README change.
4. C2.3 red, then C2.3 green with its README change.

Then bump the version so the operator can reinstall and test live, and continue with Phase E.

---

## Phase D — OPTIONAL: agent-only git hook backstop (run only on the operator's go-ahead)

Skip this phase unless, after Phase C, the operator decides the documented residual is not
acceptable. Operator requirements for it:

- It must block **only Claude Code**. Claude Code sets `CLAUDECODE=1` in the Bash tool's
  environment, verified on 2.1.283. A commit without that variable (the operator's terminal) is
  never touched.
- A config setting must turn it off.
- It needs uninstall instructions.

Measured on git 2.50 on 2026-09-25 (`pre-commit`/`commit-msg`/`pre-merge-commit` probes):

| command | fires |
|---|---|
| `commit` (any spelling, wrapper, alias, subprocess) | pre-commit, commit-msg |
| `merge` | pre-merge-commit, commit-msg |
| `cherry-pick`, `revert`, `commit-tree`+`update-ref`, `commit --no-verify` | none |
| `am` | applypatch-msg |

So the backstop covers commit and merge from **any** process, including a script file or
`python`. It does not cover cherry-pick/revert or `--no-verify`/`commit-tree`: Phase C already
refuses those when they are visible in the command text. `reference-transaction` (git ≥2.28)
would see every ref update, but Ubuntu 20.04's git 2.25 lacks it and it fires for fetches and
checkouts too, so it is out of scope.

### Task 9: Install/uninstall a chained, config-controlled hook

**Files:**
- Create: `reviewer_githook.py` (stdlib-only; `install(root)`, `uninstall(root)`,
  `enabled()`, `HOOK_NAMES`)
- Modify: `toggle.py` `_on` (install when `enabled()`) and the `off` branch (always uninstall)
- Modify: `reviewer_git.py` `run_commit` (pass the one-use nonce in `env`)
- Modify: `AGENT_UNINSTALL.md`, `README.md` (config key and uninstall)
- Modify: `tools/release_export.py` (ship `reviewer_githook.py`)
- Test: `tests/test_githook_backstop.py` (new)

**Interfaces:**
- Produces: config key `gitHookBackstop` (bool, default `true` once this phase ships) and env
  override `REVIEWER_GIT_HOOK_BACKSTOP=0`.
- Produces: `reviewer_githook.install(root) -> list[str]` (the hook paths written) and
  `reviewer_githook.uninstall(root) -> list[str]` (the paths removed or restored).
- Produces: `reviewer_githook.issue_nonce() -> (nonce, path)` and
  `reviewer_githook.revoke_nonce(path)`.
- Consumes: `rl.reviewer_home()`, `rl.config_get()`.

Design:

- **Location.** Use `git -C <root> rev-parse --git-path hooks` (git 2.5+). It honours
  `core.hooksPath`, so a husky-style hooks directory is chained rather than bypassed.
- **Hooks.** `pre-commit` and `pre-merge-commit` (2.24+, present on 2.25).
- **Chaining.** An existing hook that is not ours is renamed to `<name>.reviewer-agent-chained`.
  Ours runs its check, then `exec`s the chained one with the same arguments. Uninstall restores
  it. Ours is identified by the header line `# reviewer-agent managed hook v1`.
- **Nonce.** `run_commit` creates a random 32-hex nonce file at
  `<reviewer_home>/state/gate_nonces/<nonce>` (mode 0600). It passes `REVIEWER_GATE_NONCE=<nonce>`
  in the commit's `env` and deletes the file in a `finally`. The file exists only for the gate's
  own commit, so a forged `REVIEWER_GATE_NONCE=x` fails. The residual (reading the nonce during
  that window) is documented.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_githook_backstop.py
# SPDX-License-Identifier: MPL-2.0
"""OPTIONAL Phase D: a git-level backstop that refuses Claude Code commits the gate did not make."""
import os
import subprocess

import reviewer_githook as gh


def _repo(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True)
    (repo / "f").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "f"], check=True)
    return repo


def _commit(repo, env_extra):
    env = dict(os.environ)
    env.pop("CLAUDECODE", None)
    env.update(env_extra)
    return subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "m"],
                          capture_output=True, text=True, env=env)


def test_an_agent_commit_without_the_nonce_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = _repo(tmp_path)
    gh.install(str(repo))
    p = _commit(repo, {"CLAUDECODE": "1"})
    assert p.returncode != 0 and "reviewerAgent" in p.stderr


def test_the_operators_terminal_commit_is_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = _repo(tmp_path)
    gh.install(str(repo))
    assert _commit(repo, {}).returncode == 0


def test_a_forged_nonce_is_refused_and_a_live_one_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = _repo(tmp_path)
    gh.install(str(repo))
    assert _commit(repo, {"CLAUDECODE": "1", "REVIEWER_GATE_NONCE": "0" * 32}).returncode != 0
    nonce, path = gh.issue_nonce()
    try:
        assert _commit(repo, {"CLAUDECODE": "1", "REVIEWER_GATE_NONCE": nonce}).returncode == 0
    finally:
        gh.revoke_nonce(path)


def test_an_existing_hook_is_chained_and_restored(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    repo = _repo(tmp_path)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho theirs >&2\nexit 1\n")
    hook.chmod(0o755)
    gh.install(str(repo))
    assert "theirs" in _commit(repo, {}).stderr  # still runs for the operator
    gh.uninstall(str(repo))
    assert hook.read_text().startswith("#!/bin/sh\necho theirs")


def test_the_config_setting_disables_it(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_GIT_HOOK_BACKSTOP", "0")
    assert gh.enabled() is False
```

- [ ] **Step 2: Run to confirm red; commit red**

Run: `python3 -m pytest tests/test_githook_backstop.py -q -p no:cacheprovider`
Expected: FAIL (no module).

```bash
git add tests/test_githook_backstop.py
```
```bash
git commit -m "test: pin an optional agent-only git hook backstop that a config setting disables"
```

- [ ] **Step 3: Implement `reviewer_githook.py`**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""OPTIONAL git-level backstop for the commit gate (docs/plans/claude_linux_issues.md, Phase D).

Refuses a commit or merge made from inside Claude Code (CLAUDECODE=1) unless the gate made it
(a live one-use nonce). The operator's own terminal commits are never touched. Off with
`gitHookBackstop: false` in config.json or REVIEWER_GIT_HOOK_BACKSTOP=0."""
import os
import secrets
import subprocess
from pathlib import Path

import reviewer_lib as rl

MARK = "# reviewer-agent managed hook v1"
HOOK_NAMES = ("pre-commit", "pre-merge-commit")
CHAINED = ".reviewer-agent-chained"

_BODY = """#!/bin/sh
{mark}
if [ -n "$CLAUDECODE" ]; then
  if [ -z "$REVIEWER_GATE_NONCE" ] || [ ! -f "{nonces}/$REVIEWER_GATE_NONCE" ]; then
    echo "reviewerAgent: commits in this repository go through the review gate. Stage the change and run a plain \\`git commit -m ...\\` as its own Bash call." >&2
    exit 1
  fi
fi
if [ -x "$0{chained}" ]; then exec "$0{chained}" "$@"; fi
exit 0
"""


def enabled():
    env = os.environ.get("REVIEWER_GIT_HOOK_BACKSTOP")
    if env is not None:
        return env.strip() not in ("0", "false", "off", "")
    return bool(rl.config_get("gitHookBackstop", True))


def _nonce_dir():
    return rl.reviewer_home() / "state" / "gate_nonces"


def _hooks_dir(root):
    p = subprocess.run(["git", "-C", str(root), "rev-parse", "--git-path", "hooks"],
                       capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        raise RuntimeError("cannot locate the hooks directory: " + p.stderr.strip())
    path = Path(p.stdout.strip())
    return path if path.is_absolute() else Path(root) / path


def install(root):
    hooks = _hooks_dir(root)
    hooks.mkdir(parents=True, exist_ok=True)
    written = []
    for name in HOOK_NAMES:
        hook = hooks / name
        if hook.exists() and MARK not in hook.read_text(errors="replace"):
            hook.rename(hooks / (name + CHAINED))
        hook.write_text(_BODY.format(mark=MARK, nonces=_nonce_dir(), chained=CHAINED))
        hook.chmod(0o755)
        written.append(str(hook))
    return written


def uninstall(root):
    try:
        hooks = _hooks_dir(root)
    except RuntimeError:
        return []
    changed = []
    for name in HOOK_NAMES:
        hook, chained = hooks / name, hooks / (name + CHAINED)
        if hook.exists() and MARK in hook.read_text(errors="replace"):
            hook.unlink()
            changed.append(str(hook))
        if chained.exists() and not hook.exists():
            chained.rename(hook)
            changed.append(str(hook))
    return changed


def issue_nonce():
    d = _nonce_dir()
    d.mkdir(parents=True, exist_ok=True)
    nonce = secrets.token_hex(16)
    path = d / nonce
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    return nonce, path


def revoke_nonce(path):
    try:
        Path(path).unlink()
    except OSError:
        pass
```

In `reviewer_git.run_commit` (`:673`), add an optional `env=None` parameter and pass it to
`Popen`. `reviewer_git` stays free of package imports: the caller in `commit_hook.py` issues the
nonce.

```python
    nonce, nonce_path = (None, None)
    if reviewer_githook.enabled():
        nonce, nonce_path = reviewer_githook.issue_nonce()
    try:
        env = dict(os.environ, REVIEWER_GATE_NONCE=nonce) if nonce else None
        result = rl.run_commit(cwd, commit_request["message"], commit_request["options"], env=env)
    finally:
        if nonce_path:
            reviewer_githook.revoke_nonce(nonce_path)
```

In `toggle.py`, after `rl.set_active(root)` in `_on`:
`if reviewer_githook.enabled(): reviewer_githook.install(root)` and print one line naming the hooks
and the off switch. In the `off` branch: `reviewer_githook.uninstall(root)` (always, even when the
setting is now false).

In `AGENT_UNINSTALL.md`, add a step before plugin removal: run `/reviewerAgent off` in each gated
repository (this removes the hooks and restores any chained hook). For a repository that was
deleted or never turned off, give the manual fallback: in `$(git rev-parse --git-path hooks)`,
delete `pre-commit`/`pre-merge-commit` if their second line is
`# reviewer-agent managed hook v1`, then rename `*.reviewer-agent-chained` back.

- [ ] **Step 4: Run and commit green**

Run: `python3 -m pytest tests/test_githook_backstop.py tests/test_gate_owns_commit.py tests/test_commit_git_helpers.py tests/test_release_export.py -q -p no:cacheprovider`
Also run it under Docker `ubuntu:20.04` (git 2.25) with `sh tools/ci_old_git.sh`.
Expected: PASS on both.

```bash
git add reviewer_githook.py toggle.py commit_hook.py reviewer_git.py AGENT_UNINSTALL.md README.md tools/release_export.py
```
```bash
git commit -m "feat: optional agent-only git hook backstop for the commit gate, off via gitHookBackstop"
```

---

## Phase E — Stop supervision

### Task 10: Judge the turn's real final message (#17)

**Files:**
- Modify: `reviewer_stopcontinue.py:101-122` (`last_assistant_text`; new `_ends_with_text`)
- Modify: `stop_hook.py:100` (pass the payload's `last_assistant_message`)
- Test: `tests/test_reviewer_lib.py` (next to `test_last_assistant_text_retries_until_text_flushes`
  at `:1412`)

**Interfaces:**
- Produces: `last_assistant_text(transcript_path, retries=3, retry_delay=0.3, final_message=None) -> str`.

Claude Code 2.1.283's Stop hook input has `last_assistant_message` ("Text content of the last
assistant message before stop"). When present, it is authoritative for the final message. The
transcript still supplies the turn's earlier text.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_reviewer_lib.py, beside test_last_assistant_text_retries_until_text_flushes
def _text_event(text):
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def _thinking_event():
    return {"type": "assistant", "message": {"role": "assistant",
                                             "content": [{"type": "thinking", "thinking": "..."}]}}


def test_a_non_empty_stale_read_waits_for_the_closing_summary(tmp_path, monkeypatch):
    # The 2026-09-24 case: the opening paragraph was on disk, the summary landed 0.3 s later.
    path = _transcript(tmp_path, _text_event("Running everything now: #15, then #12"),
                       _thinking_event())

    def flush(_secs):
        with open(path, "a") as fh:
            fh.write(_json.dumps(_text_event("Summary: all probes done")) + "\n")
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", flush)
    text = rl.last_assistant_text(path)
    assert text.endswith("Summary: all probes done")
    assert "Running everything now" in text


def test_the_payload_final_message_is_used_when_the_transcript_never_catches_up(
        tmp_path, monkeypatch):
    path = _transcript(tmp_path, _text_event("Running everything now"), _thinking_event())
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", lambda _s: None)
    text = rl.last_assistant_text(path, final_message="Summary: done")
    assert text.endswith("Summary: done") and "Running everything now" in text


def test_a_flushed_final_message_needs_no_wait(tmp_path, monkeypatch):
    path = _transcript(tmp_path, _text_event("Opening"), _text_event("Summary: done"))

    def fail(_s):
        raise AssertionError("must not wait once the final message is on disk")
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", fail)
    assert rl.last_assistant_text(path, final_message="Summary: done").endswith("Summary: done")
    assert rl.last_assistant_text(path).endswith("Summary: done")


def test_a_final_phrase_seen_earlier_in_the_turn_does_not_settle_a_stale_read(
        tmp_path, monkeypatch):
    # The phrase appeared mid-turn; the transcript still ends with stale output. Membership would
    # return the stale text; a terminal match waits, then appends the real final message.
    path = _transcript(tmp_path, _text_event("Plan: finish, then write All done."),
                       _text_event("Running the probes"), _thinking_event())
    waits = []
    monkeypatch.setattr(reviewer_stopcontinue, "_sleep", waits.append)
    text = rl.last_assistant_text(path, final_message="All done.")
    assert waits, "a stale read must be re-read"
    assert text.endswith("All done.") and "Running the probes" in text
```

(`_transcript`, `_json` and the `reviewer_stopcontinue` import already exist in that file for the
neighbouring flush test. Reuse them.)

- [ ] **Step 2: Run red, commit red**

Run: `python3 -m pytest tests/test_reviewer_lib.py -k "stale_read or payload_final or needs_no_wait or seen_earlier" -q -p no:cacheprovider`
Expected: FAIL.

```bash
git add tests/test_reviewer_lib.py
```
```bash
git commit -m "test: pin that a non-empty but stale transcript read is not judged as the final message"
```

- [ ] **Step 3: Implement**

```python
def _ends_with_text(lines):
    """True when the newest assistant-side event carries text, i.e. the closing message has
    landed. A trailing thinking/tool_use event, or a tool result after the last text, means the
    turn's final text may still be in flight."""
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "assistant":
            return bool(_assistant_event_text(ev))
        if ev.get("type") == "user":
            return False
    return False


def last_assistant_text(transcript_path, retries=3, retry_delay=0.3, final_message=None):
    """Concatenated text of the final TURN's assistant messages in a Claude Code transcript
    (JSONL), or '' if none / unreadable / the final turn is genuinely tool-only.

    - TURN-scoped, not last-event-scoped (2026-07-21).
    - Flush race: the Stop event can fire before the closing text is written. "Non-empty" is not
      "complete" (2026-09-24: the supervisor judged a turn by its opening paragraph and ordered
      finished work redone). When Claude Code supplies `last_assistant_message` in the Stop
      payload (`final_message`), the read is settled once that text is on disk. Without it, the
      read is settled once the newest assistant event carries text. Otherwise wait and re-read,
      up to `retries` attempts, then append the payload's final message if it never landed."""
    final = (final_message or "").strip()
    text = ""
    for attempt in range(max(1, retries)):
        try:
            with open(transcript_path, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except (OSError, TypeError):
            return final
        text = _final_turn_text(lines)
        # A TERMINAL match, not membership: the final phrase may also have appeared earlier in
        # the turn while the transcript still ends with stale output.
        settled = (text.rstrip().endswith(final) if final
                   else (bool(text) and _ends_with_text(lines)))
        if settled:
            return text
        if attempt < retries - 1:
            _sleep(retry_delay)
    if final and not text.rstrip().endswith(final):
        return (text + "\n" + final).strip()
    return text
```

In `stop_hook.py:100`:

```python
        text = rl.last_assistant_text(data.get("transcript_path"),
                                      final_message=data.get("last_assistant_message"))
```

- [ ] **Step 4: Run the stop suites; commit green**

Run: `python3 -m pytest tests/test_reviewer_lib.py tests/test_stop_hook.py tests/test_stopcontinue_delay.py tests/test_background_wait_crossturn.py -q -p no:cacheprovider`
Expected: PASS.

```bash
git add reviewer_stopcontinue.py stop_hook.py
```
```bash
git commit -m "fix: judge a stop on the settled final message, preferring Claude Code's last_assistant_message"
```

### Task 11: Operator-only hand-offs end the turn, and a repeated directive does not loop (#19)

**Files:**
- Modify: `plugins/reviewer-agent-codex/prompts/stop_supervisor.txt` (shared; notify the Codex-plan
  session first)
- Modify: `reviewer_stopcontinue.py` (after `max_nudges`: `GENERIC_CONTINUE`, `last_directive_file`,
  `directive_repeats`, `record_directive`, `clear_directive`)
- Modify: `stop_hook.py` (`:151-153` reset; `:233-285` verdict path)
- Modify: `reviewer_lib.py` + regenerate the facade contract
- Test: `tests/test_stop_hook.py`, `tests/test_shared_stop_prompt.py`

**Interfaces:**
- Produces: `directive_repeats(proj, sid, directive, threshold=0.85) -> bool`,
  `record_directive(proj, sid, directive)`, `clear_directive(proj, sid)` and
  `GENERIC_CONTINUE: str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_shared_stop_prompt.py
def test_the_prompt_treats_operator_only_steps_as_a_hand_off():
    text = (ROOT / PLUGIN / RESOURCE).read_text(encoding="utf-8")
    assert "OPERATOR-ONLY STEPS" in text
    assert "slash command" in text
```

```python
# tests/test_stop_hook.py (reuse that file's existing stop-hook harness helpers)
import reviewer_lib as rl


def test_a_repeated_directive_lets_the_agent_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    proj, sid = str(tmp_path), "S"
    directive = "Ask the operator explicitly to run /reviewerModel effort clear and items 3-7."
    assert not rl.directive_repeats(proj, sid, directive)
    rl.record_directive(proj, sid, directive)
    assert rl.directive_repeats(proj, sid, directive + " Then record the result.")


def test_the_generic_continuation_never_counts_as_a_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.record_directive(str(tmp_path), "S", rl.GENERIC_CONTINUE)
    assert not rl.directive_repeats(str(tmp_path), "S", rl.GENERIC_CONTINUE)


def test_clear_directive_forgets_it(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    rl.record_directive(str(tmp_path), "S", "do X")
    rl.clear_directive(str(tmp_path), "S")
    assert not rl.directive_repeats(str(tmp_path), "S", "do X")
```

Add one end-to-end case using `tests/test_stop_hook.py`'s existing fake-backend harness: two
consecutive stops (the second with `stop_hook_active: true`) whose fake supervisor returns the
same directive. The first must print a `decision: block`; the second must print an info message
containing "repeated its previous directive" and no block.

- [ ] **Step 2: Run red, commit red**

Run: `python3 -m pytest tests/test_shared_stop_prompt.py tests/test_stop_hook.py -q -p no:cacheprovider`
Expected: the new cases FAIL.

```bash
git add tests/test_shared_stop_prompt.py tests/test_stop_hook.py
```
```bash
git commit -m "test: pin operator-only hand-offs and a repeat detector in stop supervision"
```

- [ ] **Step 3: Implement the prompt**

Insert into `stop_supervisor.txt` between the `WHEN STOPPING IS PERMITTED` section's last paragraph
and `COMPLETION REVIEW CONTEXT`:

```text
OPERATOR-ONLY STEPS

Some remaining steps can be performed only by the operator: slash commands (the agent cannot issue them), restarting or reconfiguring the host application, interactive logins, and actions in the operator's own tools or accounts. When every remaining step is of this kind, stopping is permitted under the second case if the proposed final message names those steps concretely and asks the operator to perform them. Do not direct the agent to ask again, to rephrase the request, or to verify something only the operator can check; a proposed final message that already contains the request satisfies this rule.
```

- [ ] **Step 4: Implement the repeat detector**

In `reviewer_stopcontinue.py`, after `max_nudges` (add `import difflib` at the top):

```python
# The prompt's own generic continuation. It is identical every time BY DESIGN, so it can never
# count as a repeat.
GENERIC_CONTINUE = ("You said that there is more work to do, and it does not appear to require "
                    "additional operator approval. In that case, continue your work.")


def _normalize_directive(text):
    return " ".join((text or "").lower().split())


def last_directive_file(proj, claude_session_id):
    return nudge_count_file(proj, claude_session_id).with_suffix(".last_directive")


def directive_repeats(proj, claude_session_id, directive, threshold=0.85):
    """True when `directive` restates the previous continue directive of this stop chain.
    Re-issuing it cannot help: the agent already answered it (2026-09-24: three identical "ask
    the operator" directives for a message that already asked)."""
    now = _normalize_directive(directive)
    if not now or now == _normalize_directive(GENERIC_CONTINUE):
        return False
    try:
        prev = last_directive_file(proj, claude_session_id).read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(prev) and difflib.SequenceMatcher(None, prev, now).ratio() >= threshold


def record_directive(proj, claude_session_id, directive):
    f = last_directive_file(proj, claude_session_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(_normalize_directive(directive), encoding="utf-8")


def clear_directive(proj, claude_session_id):
    last_directive_file(proj, claude_session_id).unlink(missing_ok=True)
```

In `stop_hook.py`:

- At `:152-153`: `if not stop_active: rl.reset_nudge_count(cwd, claude_sid); rl.clear_directive(cwd, claude_sid)`.
- Immediately after the `rl.log("[stop] key={} backend={} ...` block (`:248-255`) and **before** the
  pre-enactment delay, add:

```python
    if keep_going and stop_active and rl.directive_repeats(cwd, claude_sid, out):
        rl.log("[stop] key={} supervisor repeated its previous directive -> allow stop".format(key))
        print(json.dumps(rl.info_output(
            "[stopContinue supervisor] repeated its previous directive, which the agent has "
            "already answered; letting it stop so the operator can respond:\n" + rl.short(out))))
        rl.clear_directive(cwd, claude_sid)
        return 0
```

- In the `if keep_going:` branch (`:267`), add `rl.record_directive(cwd, claude_sid, out)` beside
  `rl.bump_nudge_count(...)`.

Export the four new names in `reviewer_lib.py`; run `python3 tools/gen_facade_contract.py`.

- [ ] **Step 5: Run and commit green**

Run: `python3 -m pytest tests/test_shared_stop_prompt.py tests/test_stop_hook.py tests/test_facade_contract.py tests/test_stopcontinue_delay.py -q -p no:cacheprovider`
Then from `plugins/reviewer-agent-codex`: `python -m pytest -p no:cacheprovider -q`. The prompt is
shared, and the Codex suite must stay green.
Expected: PASS.

```bash
git add plugins/reviewer-agent-codex/prompts/stop_supervisor.txt reviewer_stopcontinue.py stop_hook.py reviewer_lib.py tests/test_facade_contract.py
```
```bash
git commit -m "fix: let an operator-only hand-off end the turn and stop re-issuing a directive the agent already answered"
```

### Task 12: Bound supervisor cost and retire sessions on a model change

**Files:**
- Modify: `reviewer_state.py` (after `retire_commit_reviewer_session`: `stop_rotate_every`,
  `record_stop_review`, `retire_stop_session`)
- Modify: `stop_hook.py` (after the verdict log line)
- Modify: `reviewer_model_toggle.py` (set / clear / effort branches)
- Modify: `reviewer_lib.py` + regenerate the facade contract; `README.md` (config key
  `stopRotateEvery`)
- Test: `tests/test_session_roles.py`, `tests/test_reviewer_model_toggle.py`

**Interfaces:**
- Produces: `stop_rotate_every() -> int` (env `REVIEWER_STOP_ROTATE_EVERY`, config
  `stopRotateEvery`, default 6, 0 disables), `record_stop_review(proj, sid) -> int` and
  `retire_stop_session(proj, sid) -> list[str]`.

Cause of the 2.5k → 152k growth: each stop re-sends up to 40k characters of operator transcript
(`operator_transcript_block`) into one **resumed** `ROLE_STOP` session. Scheduled rotation retires
only `ROLE_REVIEWER`, so that session is never retired automatically. The per-call prompt is
self-contained, so retiring the stop session loses nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session_roles.py
def test_the_stop_session_is_retired_after_n_reviews(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_STOP_ROTATE_EVERY", "3")
    proj, sid = str(tmp_path), "S"
    rl.write_session_id_if_absent(proj, sid, "STOP-1", role=rl.ROLE_STOP)
    rl.write_session_id_if_absent(proj, sid, "REV-1", role=rl.ROLE_REVIEWER)
    assert [rl.record_stop_review(proj, sid) for _ in range(3)] == [1, 2, 3]
    cleared = rl.retire_stop_session(proj, sid)
    assert cleared and rl.read_session_id(proj, sid, rl.ROLE_STOP) is None
    assert rl.read_session_id(proj, sid, rl.ROLE_REVIEWER) == "REV-1"  # commit reviewer kept
    assert rl.record_stop_review(proj, sid) == 1  # the count restarts
```

(Use the codex session writer that `tests/test_session_roles.py` already uses. If
`write_session_id_if_absent` does not exist under that name, use the writer that file's other
tests use.)

```python
# tests/test_reviewer_model_toggle.py
def test_changing_the_model_retires_this_sessions_reviewers(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    monkeypatch.setenv("REVIEWER_HOME", home)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "S")
    proj = str(tmp_path)
    rl.write_session_id_if_absent(proj, "S", "STOP-1", role=rl.ROLE_STOP)
    p = run_toggle(["gpt-5.5"], home, extra_env={"CLAUDE_CODE_SESSION_ID": "S",
                                                  "CLAUDE_PROJECT_DIR": proj})
    assert p.returncode == 0, p.stderr
    assert rl.read_session_id(proj, "S", rl.ROLE_STOP) is None
    assert "retired" in p.stdout
```

(Adapt `run_toggle` to accept `extra_env` if it does not already.)

- [ ] **Step 2: Run red; commit red**

Run: `python3 -m pytest tests/test_session_roles.py tests/test_reviewer_model_toggle.py -k "retire" -q -p no:cacheprovider`
Expected: FAIL.

```bash
git add tests/test_session_roles.py tests/test_reviewer_model_toggle.py
```
```bash
git commit -m "test: pin stop-session retirement after N reviews and on a model change"
```

- [ ] **Step 3: Implement**

```python
# reviewer_state.py, after retire_commit_reviewer_session
def stop_rotate_every():
    """Retire the stopContinue supervisor after this many reviews (0 = never). Each stop resends
    the operator transcript into one RESUMED session, so its context -- and cost -- grows with
    every stop (observed 2.5k -> 152k tokens); the per-call prompt is self-contained, so a fresh
    session loses nothing."""
    return _tunable_int("REVIEWER_STOP_ROTATE_EVERY", "stopRotateEvery", 6, 0)


def _stop_review_count_file(proj, claude_session_id):
    stem = _project_session_stem(proj, claude_session_id)
    return None if stem is None else reviewer_home() / "state" / (stem + ".stop-reviews")


def record_stop_review(proj, claude_session_id):
    f = _stop_review_count_file(proj, claude_session_id)
    if f is None:
        return 0
    try:
        n = int(f.read_text().strip() or "0") + 1
    except (OSError, ValueError):
        n = 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(n) + "\n")
    return n


def retire_stop_session(proj, claude_session_id):
    """Retire only the stopContinue supervisor, preserving the commit reviewer."""
    sid = (claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return []
    cleared = []
    for f in (session_id_file(proj, sid, ROLE_STOP), pi_session_file(proj, sid, ROLE_STOP),
              claude_session_file(proj, sid, ROLE_STOP),
              _stop_review_count_file(proj, sid)):
        if f is not None and f.exists():
            try:
                f.unlink()
            except OSError:
                continue
            cleared.append(str(f))
    return cleared
```

In `stop_hook.py`, after the verdict `rl.log(...)` (`:248-255`):

```python
    if code == 0 and out.strip():
        every = rl.stop_rotate_every()
        if every and rl.record_stop_review(cwd, claude_sid) >= every:
            rl.retire_stop_session(cwd, claude_sid)
            rl.log("[stop] key={} stop session retired after {} reviews".format(key, every))
```

In `reviewer_model_toggle.py`, after each successful write (set model, set effort, `clear`,
`effort clear`):

```python
    sid = rl.claude_session_id_from_env()
    if sid and rl.rotate_session(rl.project_dir(), sid):
        print("reviewerModel: retired this project's reviewer sessions for this Claude session so "
              "the new setting starts fresh; other sessions pick it up at their next rotation.")
```

Add `stopRotateEvery` to the README config table. Export the new names; regenerate the facade
contract.

- [ ] **Step 4: Run and commit green**

Run: `python3 -m pytest tests/test_session_roles.py tests/test_reviewer_model_toggle.py tests/test_stop_hook.py tests/test_facade_contract.py tests/test_config.py -q -p no:cacheprovider`
Expected: PASS.

```bash
git add reviewer_state.py stop_hook.py reviewer_model_toggle.py reviewer_lib.py tests/test_facade_contract.py README.md
```
```bash
git commit -m "fix: retire the stop supervisor every N reviews and all role sessions on a model change"
```

---

## Phase F — Commands

### Task 13: `/reviewerDirective` works when the hook sees the raw command (#18); one ARM per `on`

**Files:**
- Modify: `userpromptsubmit_hook.py` (`_directive_block`, `_rearm_context`)
- Modify: `skills/reviewerDirective/SKILL.md` (add a loud "not recorded" fallback line)
- Test: `tests/test_directives.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_directives.py
import json
import os
import subprocess
import sys
from pathlib import Path

UPS = str(Path(__file__).resolve().parents[1] / "userpromptsubmit_hook.py")


def _submit(prompt, proj, home):
    env = dict(os.environ, REVIEWER_HOME=home, CLAUDE_PROJECT_DIR=proj)
    return subprocess.run([sys.executable, UPS],
                          input=json.dumps({"cwd": proj, "session_id": "S", "prompt": prompt}),
                          capture_output=True, text=True, env=env)


@pytest.mark.parametrize("prefix", ["/reviewer-agent:reviewerDirective",
                                    "/reviewerDirective",
                                    "<<reviewer-agent directive>>"])
def test_the_hook_records_a_directive_from_the_raw_prompt(tmp_path, prefix):
    proj = make_repo(tmp_path)  # this file's existing repo helper
    home = str(tmp_path / "rh")
    p = _submit(prefix + " deploys to staging are pre-authorized", proj, home)
    assert json.loads(p.stdout)["decision"] == "block"
    assert "deploys to staging are pre-authorized" in _submit(prefix + " list", proj, home).stdout


def test_directive_text_is_recorded_verbatim_and_never_executed(tmp_path):
    # Review Focus 4.
    proj = make_repo(tmp_path)
    home = str(tmp_path / "rh")
    canary = tmp_path / "pwned"
    text = "note $(touch {}) `touch {}`".format(canary, canary)
    _submit("/reviewerDirective " + text, proj, home)
    assert not canary.exists()
    assert text in _submit("/reviewerDirective list", proj, home).stdout


def test_a_prompt_merely_mentioning_the_command_is_not_intercepted(tmp_path):
    proj = make_repo(tmp_path)
    p = _submit("what does /reviewerDirective do?", proj, str(tmp_path / "rh"))
    assert '"decision"' not in p.stdout
```

(If `make_repo` is not in `tests/test_directives.py`, use the repo builder it already has.)

- [ ] **Step 2: Run red; commit red**

Run: `python3 -m pytest tests/test_directives.py -q -p no:cacheprovider`
Expected: the raw-prefix cases FAIL.

```bash
git add tests/test_directives.py
```
```bash
git commit -m "test: drive the UserPromptSubmit hook with the raw reviewerDirective prompt"
```

- [ ] **Step 3: Implement**

```python
# userpromptsubmit_hook.py
import re

# Claude Code 2.1.278+ hands this hook the operator's RAW prompt (`/reviewer-agent:reviewerDirective
# <text>`), not the expanded skill body, so the sentinel alone never matched (2026-09 Ubuntu
# report: a silent no-op). Match both; either way the text arrives as JSON, never via a shell.
_DIRECTIVE_COMMAND_RE = re.compile(r"\A\s*/(?:reviewer-agent:)?reviewerDirective(?=\s|\Z)")
_WATCHDOG_COMMAND_RE = re.compile(r"\A\s*/(?:reviewer-agent:)?stopContinueWatchdog(?=\s|\Z)")


def _directive_remainder(prompt):
    if prompt.lstrip().startswith(DIRECTIVE_SENTINEL):
        return prompt.split(DIRECTIVE_SENTINEL, 1)[1].strip()
    m = _DIRECTIVE_COMMAND_RE.match(prompt)
    return prompt[m.end():].strip() if m else None
```

In `_directive_block`, replace the first three lines of the body with:

```python
    prompt = str(data.get("prompt") or "")
    remainder = _directive_remainder(prompt)
    if remainder is None:
        return None
```

In `_rearm_context`, right after the tick check, add:

```python
    # `/stopContinueWatchdog ...` prints its own ARM/cancel instruction; adding ours made the
    # instruction arrive twice. An `on` also counts as liveness for one window.
    m = _WATCHDOG_COMMAND_RE.match(prompt)
    if m:
        if prompt[m.end():].strip().lower().startswith("on"):
            rl.set_watchdog_tick_seen(cwd, sid)
        return None
```

Append to `skills/reviewerDirective/SKILL.md`, after the sentinel line:

```text

If you are reading this, the directive above was NOT recorded: reviewer-agent's UserPromptSubmit hook did not intercept the command. Tell the operator exactly that, and do not act on the directive text.
```

- [ ] **Step 4: Run and commit green**

Run: `python3 -m pytest tests/test_directives.py tests/test_repository_distribution.py tests/test_stopcontinue_watchdog_toggle.py -q -p no:cacheprovider`
Expected: PASS.

```bash
git add userpromptsubmit_hook.py skills/reviewerDirective/SKILL.md
```
```bash
git commit -m "fix: record /reviewerDirective from the raw prompt and stop doubling the watchdog ARM"
```

### Task 14: `/stopContinueWatchdog status` speaks the skill's contract (#20)

**Files:**
- Modify: `stopcontinue_watchdog_toggle.py:82-87`
- Modify: `tests/test_stopcontinue_watchdog_toggle.py:6,151-157,237-245`
- Modify: `skills/stopContinueWatchdog/SKILL.md` (add one line: the cron is session-only)

- [ ] **Step 1: Change the tests (red)**

```python
def test_status_speaks_the_skill_contract(tmp_path, monkeypatch):
    home = str(tmp_path / "rh")
    proj = make_repo(tmp_path)
    enable_stopcontinue(proj, home)
    assert run("status", proj, home).stdout.strip().startswith("stopContinueWatchdog: OFF for ")
    run(["on", "5"], proj, home)
    out = run("status", proj, home).stdout.strip()
    assert out.startswith("stopContinueWatchdog: ON for ") and "(interval 5 min)" in out
```

Replace `test_status_prints_active_inactive` with this. In
`test_status_inactive_when_stopcontinue_turned_off_after_arming`, replace the two bare-string
asserts with `startswith("stopContinueWatchdog: ON")` and
`"dormant because /stopContinue is off" in ...`. Update the comment at line 6 to say status is for
the operator; the tick uses Read.

Run: `python3 -m pytest tests/test_stopcontinue_watchdog_toggle.py -q -p no:cacheprovider` → FAIL.

```bash
git add tests/test_stopcontinue_watchdog_toggle.py
```
```bash
git commit -m "test: pin watchdog status output to the skill's ON/OFF contract"
```

- [ ] **Step 2: Implement**

```python
    if action == "status" and len(argv) == 2:
        root = rl.canonical_project_key(proj)
        if rl.is_stopcontinue_watchdog_effective(root):
            print("stopContinueWatchdog: ON for {} (interval {} min).".format(
                root, rl.watchdog_interval(root)))
        elif rl.is_stopcontinue_watchdog_active(root):
            print("stopContinueWatchdog: OFF for {} (armed, but dormant because /stopContinue "
                  "is off).".format(root))
        else:
            print("stopContinueWatchdog: OFF for {}.".format(root))
        return 0
```

Add to `skills/stopContinueWatchdog/SKILL.md`: "The cron lives only in this Claude session:
it dies with the process and fires only while the session is idle. After a restart, SessionStart
re-arms it."

- [ ] **Step 3: Run and commit green**

Run: `python3 -m pytest tests/test_stopcontinue_watchdog_toggle.py -q -p no:cacheprovider` → PASS.

```bash
git add stopcontinue_watchdog_toggle.py skills/stopContinueWatchdog/SKILL.md
```
```bash
git commit -m "fix: label watchdog status output like every other watchdog command"
```

### Task 15: `/reviewerHistory clear` purges the audit log and unshared prompt parts (#21)

**Files:**
- Modify: `reviewer_audit.py` (new `purge_project_audit_data` after `append_audit_record`)
- Modify: `reviewer_history_toggle.py:24-66` (call it; update both messages)
- Modify: `PRIVACY.md:42-59`
- Test: `tests/test_review_outcomes.py` (beside `test_history_clear_purges_only_this_project`)

**Interfaces:**
- Produces: `reviewer_audit.purge_project_audit_data(proj) -> list[str]` (the paths removed).

- [ ] **Step 1: Write the failing test**

```python
def test_history_clear_purges_the_audit_log_and_only_unshared_prompt_parts(tmp_path, monkeypatch):
    # Review Focus 5.
    import reviewer_audit as ra
    import reviewer_history_toggle
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    a, b = tmp_path / "a", tmp_path / "b"
    for repo in (a, b):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    only_a = ra.store_prompt_part("diff only A reviewed")
    shared = ra.store_prompt_part("standing brief both use")
    ra.append_audit_record(str(a), {"brief_sha256": shared, "static": {"capsule_sha256": only_a}})
    ra.append_audit_record(str(b), {"brief_sha256": shared})

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    assert reviewer_history_toggle.main(["history", "clear"]) == 0

    assert not ra.audit_log_file(str(a)).exists()
    assert ra.load_prompt_part(only_a) is None
    assert ra.load_prompt_part(shared) == "standing brief both use"
    assert ra.audit_log_file(str(b)).exists()


def test_history_clear_keeps_every_prompt_part_when_a_sibling_log_is_unparseable(
        tmp_path, monkeypatch):
    # An unreadable or corrupt sibling log is unknown data, not "references nothing".
    import reviewer_audit as ra
    import reviewer_history_toggle
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    a, b = tmp_path / "a", tmp_path / "b"
    for repo in (a, b):
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
    part = ra.store_prompt_part("capsule B may also use")
    ra.append_audit_record(str(a), {"static": {"capsule_sha256": part}})
    ra.append_audit_record(str(b), {"brief_sha256": part})
    with ra.audit_log_file(str(b)).open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")

    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    assert reviewer_history_toggle.main(["history", "clear"]) == 0

    assert not ra.audit_log_file(str(a)).exists()
    assert ra.load_prompt_part(part) == "capsule B may also use"
```

(Add `import subprocess` to the file if it is missing.)

Run: `python3 -m pytest tests/test_review_outcomes.py -k "unshared or unparseable" -q -p no:cacheprovider` → FAIL.

```bash
git add tests/test_review_outcomes.py
```
```bash
git commit -m "test: pin that history clear removes the audit log and prompt parts no other project uses"
```

- [ ] **Step 2: Implement**

```python
# reviewer_audit.py, after append_audit_record
def _shas_in(value, found):
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and str(key).endswith("sha256") and _SHA256.match(item):
                found.add(item)
            else:
                _shas_in(item, found)
    elif isinstance(value, list):
        for item in value:
            _shas_in(item, found)
    return found


def _audit_shas(path):
    """Every prompt-part sha256 `path` references, or None when the log cannot be read or any
    non-blank line fails to parse. None means "unknown", never "references nothing"."""
    found = set()
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    _shas_in(json.loads(line), found)
                except ValueError:
                    return None
    except (OSError, UnicodeDecodeError):
        return None
    return found


def purge_project_audit_data(proj):
    """Delete this project's audit log and every stored prompt part ONLY it referenced.

    Parts are content-addressed and shared across projects (a brief or capsule two repositories
    both used is one file), so a part is removed only when no OTHER project's audit log names it.
    One of them can hold a unified diff of reviewed code, which is what `/reviewerHistory clear`
    promises to purge. If ANY sibling log cannot be listed, read or parsed, no part is removed:
    "unshared" is never inferred from incomplete data. The project's own log is still removed."""
    mine = audit_log_file(proj)
    own = _audit_shas(mine) or set()  # an unreadable own log only means fewer deletions
    shared = set()
    try:
        others = [p for p in mine.parent.glob("*.audits.jsonl") if p != mine]
    except OSError:
        others = None
    for other in others or []:
        refs = _audit_shas(other)
        if refs is None:
            others = None
            break
        shared |= refs
    removed = []
    for sha in (sorted(own - shared) if others is not None else []):
        part = prompt_parts_dir() / (sha + ".txt")
        try:
            part.unlink()
        except OSError:
            continue
        removed.append(str(part))
    try:
        mine.unlink()
        removed.append(str(mine))
    except OSError:
        pass
    return removed
```

In `reviewer_history_toggle._clear`: `import reviewer_audit`, then
`removed = rl.clear_review_history(proj) + reviewer_audit.purge_project_audit_data(proj)`. Change
the success text to "(retained reviews, saved rulings, the audit log, and stored prompt parts no
other project uses)". Change the show text (`:44-45`) the same way.

In `PRIVACY.md`, name `reviews/<project>.audits.jsonl` and `reviews/prompt_parts/` in the data
inventory, and state that `/reviewerHistory clear` removes the project's audit log and the prompt
parts no other project references. A part another project still references is kept; clearing
that project too removes it. If another project's audit log cannot be read or parsed, every part
is kept, because the gate cannot tell which parts that project uses.

- [ ] **Step 3: Run and commit green**

Run: `python3 -m pytest tests/test_review_outcomes.py tests/test_audit_record.py tests/test_public_documents.py -q -p no:cacheprovider` → PASS.

```bash
git add reviewer_audit.py reviewer_history_toggle.py PRIVACY.md
```
```bash
git commit -m "fix: purge the audit log and unshared prompt parts on /reviewerHistory clear"
```

### Task 16: Find `codex` on PATH (#4, Linux)

**Files:**
- Modify: `reviewer_state.py:96-101` (`codex_bin`)
- Modify: `README.md:1176` (drop the stale backlog line; add `codexBin` to the config table if absent)
- Modify: `reviewer_lib.py:12` docstring (default resolution order)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_codex_is_found_on_path_when_not_in_local_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_BIN", raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # no ~/.local/bin/codex
    bindir = tmp_path / "npm-global"
    bindir.mkdir()
    codex = bindir / "codex"
    codex.write_text("#!/bin/sh\n")
    codex.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir))
    assert rl.codex_bin() == str(codex)


def test_local_bin_still_wins_when_present(tmp_path, monkeypatch):
    monkeypatch.delenv("REVIEWER_CODEX_BIN", raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    local = home / ".local" / "bin" / "codex"
    local.write_text("#!/bin/sh\n")
    monkeypatch.setenv("HOME", str(home))
    assert rl.codex_bin() == str(local)
```

Run: `python3 -m pytest tests/test_config.py -k codex_is_found -q -p no:cacheprovider` → FAIL.

```bash
git add tests/test_config.py
```
```bash
git commit -m "test: pin a PATH fallback for the codex binary"
```

- [ ] **Step 2: Implement**

```python
def codex_bin():
    """REVIEWER_CODEX_BIN, then config.json `codexBin`, then ~/.local/bin/codex when it exists,
    then `codex` on PATH (npm-global /usr/local/bin is common on Linux). The ~/.local/bin path is
    the last resort so an error names a concrete location."""
    explicit = os.environ.get("REVIEWER_CODEX_BIN") or config_get("codexBin")
    if explicit:
        return explicit
    local = Path.home() / ".local" / "bin" / "codex"
    if local.exists():
        return str(local)
    return shutil.which("codex") or str(local)
```

- [ ] **Step 3: Run and commit green**

Run: `python3 -m pytest tests/test_config.py tests/test_claude_backend.py tests/test_host_versions.py -q -p no:cacheprovider` → PASS.

```bash
git add reviewer_state.py README.md reviewer_lib.py
```
```bash
git commit -m "fix: fall back to codex on PATH when ~/.local/bin/codex does not exist"
```

### Task 17: Scope wording (small items)

**Files:**
- Modify: `reviewer_session_toggle.py:91-93`
- Modify: `skills/reviewerModel/SKILL.md:25`

- [ ] **Step 1: Edit the strings**

`reviewer_session_toggle.py`:
"`/reviewerSession rotate` retires these role sessions for THIS project in THIS Claude session;
other Claude sessions and other repositories keep their own reviewers."

`skills/reviewerModel/SKILL.md:25`:
"The model and effort are global per backend: they apply to every project that uses that backend,
not only this one."

- [ ] **Step 2: Check tests that pin those strings**

Run: `grep -rn "FOR THIS PROJECT\|scoped to the project's backend" tests/`. Update any hit to the
new wording, then run
`python3 -m pytest tests/test_reviewer_session.py tests/test_reviewer_model_toggle.py tests/test_repository_distribution.py -q -p no:cacheprovider` → PASS.

- [ ] **Step 3: Commit**

```bash
git add reviewer_session_toggle.py skills/reviewerModel/SKILL.md
```
```bash
git commit -m "docs: state the real scope of session rotation and model settings"
```

---

## Phase G — Install path

### Task 18: A sub-minute smoke subset for install-time verification (#5)

**Files:**
- Modify: `pytest.ini` (register the marker)
- Modify (add `pytestmark = pytest.mark.smoke` at module level): `tests/test_facade_contract.py`,
  `tests/test_repository_distribution.py`, `tests/test_host_versions.py`,
  `tests/test_commit_request.py`, `tests/test_commit_intent.py`, `tests/test_hook_launcher.py`,
  `tests/test_gate_owns_commit.py`, `tests/test_reviewer_cmd_dispatcher.py`
- Create: `tests/test_smoke_subset.py`

- [ ] **Step 1: Write the failing guard test**

```python
# tests/test_smoke_subset.py
# SPDX-License-Identifier: MPL-2.0
"""`pytest -m smoke` is what AGENT_INSTALL.md step 3 runs: under a minute, covering imports,
distribution shape, host versions, the hook launcher and one real-git gate round trip."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REQUIRED = {"test_facade_contract.py", "test_repository_distribution.py",
            "test_host_versions.py", "test_commit_request.py", "test_commit_intent.py",
            "test_hook_launcher.py", "test_gate_owns_commit.py",
            "test_reviewer_cmd_dispatcher.py"}


def test_the_smoke_subset_covers_the_install_critical_suites():
    marked = {p.name for p in TESTS.glob("test_*.py")
              if "pytestmark = pytest.mark.smoke" in p.read_text(encoding="utf-8")}
    assert REQUIRED <= marked, REQUIRED - marked


def test_the_marker_is_registered():
    assert "smoke:" in (TESTS.parent / "pytest.ini").read_text(encoding="utf-8")
```

Run → FAIL. Commit red:

```bash
git add tests/test_smoke_subset.py
```
```bash
git commit -m "test: pin an install-time smoke subset"
```

- [ ] **Step 2: Implement**

Append to `pytest.ini`:

```ini
markers =
    smoke: fast install-time subset (AGENT_INSTALL.md step 3); must stay under a minute
```

Add `import pytest` (if absent) and `pytestmark = pytest.mark.smoke` to each listed file.

- [ ] **Step 3: Measure, then commit green**

Run: `time python3 -m pytest -m smoke -q -p no:cacheprovider`
Expected: PASS in well under 60 s on this Mac. Re-measure on the 2-CPU Linux host in Task 21. If
it exceeds 60 s there, drop the slowest file from the marker set and `REQUIRED` (keeping
`test_gate_owns_commit.py`).

```bash
git add pytest.ini tests/test_facade_contract.py tests/test_repository_distribution.py tests/test_host_versions.py tests/test_commit_request.py tests/test_commit_intent.py tests/test_hook_launcher.py tests/test_gate_owns_commit.py tests/test_reviewer_cmd_dispatcher.py
```
```bash
git commit -m "feat: add a smoke marker for a sub-minute install-time test run"
```

### Task 19: The brief template carries no human preamble (#13)

**Files:**
- Modify: `reviewerAgent.md.template:1-11` (delete the preamble and the `---`; the file starts at
  "You are the REVIEWER …")
- Modify: `toggle.py` `_on` (warn about an untouched placeholder or the old preamble; never refuse)
- Test: `tests/test_brief_composition.py`, `tests/test_risk_acceptance.py` (or wherever `on` is
  driven end-to-end; use that file's `on` helper)

Aligned with `docs/plans/codex_linux_issues.md` §6: migration is by warning, not by stripping
content above `---`, and an HTML comment is not enough (the raw file is sent).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_brief_composition.py
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "reviewerAgent.md.template"


def test_the_template_sends_no_setup_instructions_to_the_reviewer():
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "Copy this file" not in text
    assert text.lstrip().startswith("You are the REVIEWER")
```

```python
# in the file that drives `/reviewerAgent on` end-to-end
def test_on_warns_when_the_brief_still_has_the_placeholder(tmp_path):
    # Build an accepted-risk repo with the TEMPLATE copied as reviewerAgent.md, using this
    # file's existing helpers, then run `on`.
    p = run_on_with_template_brief(tmp_path)
    assert p.returncode == 0
    assert "edit me" in p.stdout
```

(`run_on_with_template_brief` is a small helper to write in that file: copy
`reviewerAgent.md.template` to `<repo>/reviewerAgent.md`, record risk acceptance the way the
file's other tests do, and run `toggle.py on`.)

Run → FAIL. Commit red.

```bash
git add tests/test_brief_composition.py tests/test_risk_acceptance.py
```
```bash
git commit -m "test: pin a brief template without setup prose and an on-time placeholder warning"
```

- [ ] **Step 2: Implement**

Delete lines 1-11 of `reviewerAgent.md.template` (the "# Reviewer brief (template)" preamble and
the `---`). Move its explanation into `AGENT_INSTALL.md` step 5 (Task 20).

In `toggle.py` `_on`, after `rl.set_active(root)`:

```python
    try:
        brief = (Path(root) / "reviewerAgent.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        brief = ""
    if "(edit me)" in brief:
        print("reviewerAgent: WARNING - reviewerAgent.md still contains the template's "
              "`Project-specific guidance (edit me)` placeholder; the reviewer receives it "
              "verbatim. Replace it with this project's standards and test command.")
    if "Copy this file to the root of a project" in brief:
        print("reviewerAgent: WARNING - reviewerAgent.md begins with the old template's setup "
              "instructions, which are sent to the reviewer. Delete everything above "
              "\"You are the REVIEWER\".")
```

(Add `from pathlib import Path` if it is not already imported.)

- [ ] **Step 3: Run and commit green**

Run: `python3 -m pytest tests/test_brief_composition.py tests/test_risk_acceptance.py tests/test_reviewer_lib.py -q -p no:cacheprovider` → PASS.

```bash
git add reviewerAgent.md.template toggle.py
```
```bash
git commit -m "fix: keep setup prose out of the brief template and warn about untouched briefs on enable"
```

### Task 20: `AGENT_INSTALL.md` corrections (#2, #3, #4, #5, #6, #10, #13, #14, #23)

**Files:**
- Modify: `AGENT_INSTALL.md` (Steps 1, 2, 3, 5, 6)
- Modify: `CHANGELOG.md` (unreleased section)

Do this task **after** rebasing on the Codex-plan session's `INSTALL_WITH_AGENT.md` work, and
reuse its venv wording verbatim for consistency. `tests/test_reviewer_cmd_allow_rule.py` pins
four patterns in this file. Keep:

- an allow rule matching `Bash\(python3 [^\n)]*\*/reviewer_cmd\.py`;
- no rule ending `:*)` that contains `*/`;
- a `"deny": [...]` block matching `Bash\(python3 [^\n)]*/\*\.\.\*\)`;
- the literal `"autoMode.allow"`.

- [ ] **Step 1: Edit**

- **Step 1 (#2, #23):** after the platform checks, add:
  > If the initial working directory is not inside a Git repository, stop and ask the operator
  > which repository to protect. Then check that Git has an identity for it:
  > `git -C <TARGET_REPO> config user.email`. If that prints nothing, ask the operator to set
  > `user.name`/`user.email`; otherwise the first commit fails for a reason unrelated to the gate.
- **Step 2 (#4):** after the codex detection line, add a `codexBin` block mirroring the `piBin`
  block. Resolve with `shutil.which("codex")` under a stripped PATH
  (`env -i PATH=/usr/bin:/bin`). When the binary is not in `~/.local/bin` or on that stripped PATH,
  persist `"codexBin": "<absolute path>"` in `~/.claude/reviewer-agent/config.json` and read it
  back. Also check that `node` resolves on that PATH (codex is a node script). Delete the "put a
  non-standard path in `REVIEWER_CODEX_BIN` at step 6" instruction.
- **Step 3 (#3, #5, #6, #10):** replace the test command with the venv setup (as in
  `INSTALL_WITH_AGENT.md`) and
  `cd <PLUGIN_DIR> && <TEST_VENV>/bin/python -m pytest -m smoke -q -p no:cacheprovider -rs`.
  State: "under a minute; the full suite (about 1,300 tests, 5–12 minutes) is for
  CONTRIBUTING.md; if you run it, use background execution and wait for the exit status". Replace
  "two expected skips" with the exact list the smoke run can print. Also list the full suite's
  three skips (two reset writers in `test_split_shared_state.py`, and
  `docs/internal/REVIEWER_PROMPT_SPEC.md` absent in `test_reviewer_lib.py`). Tell the agent to
  compare `-rs` output against that list.
- **Step 5 (#13):** absorb the template's former preamble (what the file is, where it is read
  from, that `on` requires it). Tell the agent to replace the `(edit me)` paragraph and to confirm
  with `grep -c "edit me" <TARGET_REPO>/reviewerAgent.md` → `0`. Make the
  `test -e … && … || cp …` command a single line.
- **Step 6 (#14):** move the `permissions.allow`/`deny` block and the `autoMode.allow` block into a
  new **"Step 5b — offer the settings rules (agent, on approval)"**, keeping their JSON verbatim.
  Step 6 keeps only: restart, plain `/reviewerAgent on`, read DISCLAIMER, `on acceptRisk`. Soften
  the auto-mode warning to "may be blocked on some Claude Code versions".
- **CHANGELOG.md:** add an Unreleased section listing every fix in this plan, with the one
  behaviour change operators will notice: in a gated repository, `git merge`/`cherry-pick`/
  `revert`/`am`/`pull` that would commit (judged last-wins, and `pull` in rebase mode) are now
  refused with a no-commit route, and `commit-tree` is refused outright.

- [ ] **Step 2: Run the document tests and commit**

Run: `python3 -m pytest tests/test_reviewer_cmd_allow_rule.py tests/test_public_documents.py -q -p no:cacheprovider` → PASS.

```bash
git add AGENT_INSTALL.md CHANGELOG.md
```
```bash
git commit -m "docs: correct AGENT_INSTALL for non-repo cwds, git identity, codex paths, smoke tests, skips and step 6"
```

---

## Phase H — Linux acceptance and release

### Task 21: Verify on old-git Linux, then cut the release

- [ ] **Step 1: Full suites on this Mac**

Run each in the background and wait for the exit status:
- `python3 -m pytest -p no:cacheprovider -q` (root)
- `python -m pytest -p no:cacheprovider -q` from `plugins/reviewer-agent-codex`
- `python3 tools/check_split_layering.py`
- `python3 tools/release_versions.py`

Expected: all green. Any failure is investigated against `main`, not assumed pre-existing.

- [ ] **Step 2: Ubuntu 20.04 container (git 2.25.1, dash)**

Run: `docker run --rm -v "$PWD":/src -w /src ubuntu:20.04 sh tools/ci_old_git.sh`
Expected: `git version 2.25.1` and a green root suite. This covers #7, the Task 4 launcher under
dash, and the Task 6-8 classifier on old git.

- [ ] **Step 3: The reporting AWS host (Ubuntu 20.04, NFS `TMPDIR`, 2 CPUs, codex in
  `/usr/local/bin`)**

The operator runs, or authorizes, these:

1. `git pull --ff-only` in `~/reviewer-agent`. Then
   `time <venv>/bin/python -m pytest -m smoke -q -p no:cacheprovider`: under 60 s (#5). Then the
   full suite in the background with `TMPDIR` on NFS: green, including the three low-disk tests (#8).
2. Bump the version (Step 5), then `claude plugin marketplace update` + `claude plugin update`,
   and restart Claude Code.
3. With **no** `codexBin` in config, run a gated commit in `~/test`: it is reviewed,
   with no "missing binary" fail-closed (#4).
4. Re-run the report's probe table in `~/test` with the gate on:
   - `/usr/bin/git commit`, `bash -c 'git commit …'`, `git cherry-pick`, `git revert`: **DENY** with
     the no-commit route;
   - `echo "… git commit …"` and a heredoc mentioning it: **ALLOW**;
   - `git add f && git commit -m x`: the "stages and commits in one call" message (#15, #22).
5. Launch Claude Code with `env -i HOME=$HOME PATH=/nonexistent-then-fixed ...`: this is
   destructive to the session, so only on the operator's say-so. Confirm the SessionStart "no
   python3" message and that a commit is blocked with exit 2 (#16).
6. `/reviewerDirective test directive` then `/reviewerDirective list`: the directive is listed
   (#18). `/stopContinueWatchdog status`: `stopContinueWatchdog: ON for …` (#20).
   `/reviewerHistory clear`: `reviews/prompt_parts/` holds no part referenced only by this
   project, and `audits.jsonl` is gone (#21).
7. Record the outcomes (versions, commands, results) in the report file's resolution notes.

- [ ] **Step 4: Live-reviewer release matrix on this Mac** (standing authorization)

Run: `python3 tools/release_matrix.py` for the Claude-Code scenarios, following
CONTRIBUTING.md "Running the suites". Expected: all scenarios pass.

- [ ] **Step 5: Cut the version**

Follow `CONTRIBUTING.md` → "Release procedure":
1. bump `RELEASE`/`PEP440_RELEASE` in `tools/release_versions.py` and the declared versions;
2. `python3 tools/release_cachebuster.py`;
3. finish the CHANGELOG section;
4. `python3 tools/release_versions.py` → PASS.

Coordinate the number with the Codex-plan session, since the versions move in lockstep.

```bash
git add -u
```
```bash
git commit -m "release: cut the Linux-fix release for the Claude Code plugin"
```

---

## Not in this plan (and why)

- **#9 and the Codex halves of #15/#16, plus install-guide items 1, 3, 6 and 11 in
  `INSTALL_WITH_AGENT.md`:** owned by `docs/plans/codex_linux_issues.md` (in progress in another
  session).
- **"An unreachable model blocks every commit; `clear` is operator-only":** validating a model
  name needs a paid backend call per `/reviewerModel` invocation. Making `clear` model-invocable
  would split one skill into two with different invocation rules. Recovery is already one operator
  command, and the deny message names it. Revisit if it recurs.
- **Calibration datapoint (a stray marker approved):** a brief-quality observation, not a code
  defect. Operators tune it in `reviewerAgent.md`.
- **"A prose which-next menu at a stop is answered by the supervisor":** intended. The supervisor
  answers questions the operator's messages already settle (stop prompt, "QUESTIONS AND OPTIONS").
  Task 11's operator-only rule covers the genuinely unanswerable case.
- **Gate output replacing git's:** already documented (`README.md:178-181`, `:278-279`).
- **`reference-transaction` backstop:** needs git ≥2.28 (not on Ubuntu 20.04) and fires on
  fetch/checkout; excluded even from optional Phase D.
