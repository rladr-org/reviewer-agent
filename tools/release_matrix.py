#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Drive the release matrix against real hosts, in throwaway state, and report what it proved.

The unit suites answer "does this function behave"; this answers "does a person installing this
get a working gate". They fail in different ways, so the matrix runs the actual entry points --
the hook scripts a host executes, the MCP tool a Codex session calls -- against real reviewer
backends and real repositories.

ISOLATION IS THE PRECONDITION. Every scenario points `REVIEWER_STATE_DIR` /
`REVIEWER_CODEX_STATE_DIR` at a fresh temporary directory and builds its own repositories, so a
run never reads or writes the state of a live installation, and never touches an in-flight
review. Nothing here installs, uninstalls or reconfigures a host.

  python3 tools/release_matrix.py --list
  python3 tools/release_matrix.py --scenario claude-codex          # real reviewer calls
  python3 tools/release_matrix.py --all --stub                     # no model calls, fast
  python3 tools/release_matrix.py --all --report /tmp/matrix.md

`--stub` swaps the reviewer binary for the suite's deterministic stand-in. It costs nothing and
shakes the harness out, but it is NOT a release-qualifying run, for two reasons: the verdicts are
dictated rather than reached by a model, and three of the five scenarios cannot be stubbed at all
-- Pi, Claude and native Codex, whose stand-in speaks none of Pi's event stream, Claude's
`--output-format json` result entry, or `--output-last-message` -- so they are skipped. A release
needs the live run.

What this harness CANNOT do, and which therefore stays on the human checklist it prints:
pressing Escape inside a real host TUI, restarting a host to prove persistence, trusting hooks
through `/hooks`, and installing into a genuinely clean machine profile.
"""
import argparse
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import release_versions  # noqa: E402  (single source of truth for the release identity)

CODEX_PLUGIN = ROOT / "plugins" / "reviewer-agent-codex"
STUB = ROOT / "tests" / "fake_codex.py"

sys.path.insert(0, str(ROOT))
from reviewer_base import _descendants  # noqa: E402

DEFECT = '''"""A deliberately defective helper, seeded for the release matrix."""


def average(values):
    # Defect: an empty sequence raises ZeroDivisionError instead of returning a defined value.
    return sum(values) / len(values)
'''

FIXED = '''"""The corrected helper."""


def average(values):
    if not values:
        return 0.0
    return sum(values) / len(values)
'''


class Result:
    def __init__(self, name):
        self.name = name
        self.checks = []
        self.skipped = None

    def check(self, description, passed, detail=""):
        self.checks.append((description, bool(passed), detail))
        return passed

    @property
    def ok(self):
        return self.skipped is not None or all(passed for _, passed, _ in self.checks)


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo)] + list(args), capture_output=True, text=True, check=check
    )


def make_repo(path, name="matrix-repo"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "matrix@example.com")
    _git(path, "config", "user.name", "Release Matrix")
    (path / "reviewerAgent.md").write_text(
        "Review staged Python for defects. Reject anything that can raise on an empty input.\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("# {}\n".format(name), encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return path


def stage(repo, relative, content):
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repo, "add", relative)


def hook_env(state_dir, extra=None, stub=False, codex_recorder=None):
    """Environment for one hook invocation: isolated state, chosen backend binaries."""
    env = dict(os.environ)
    env.update(
        REVIEWER_STATE_DIR=str(state_dir),
        REVIEWER_HOME=str(state_dir),
        PYTHONDONTWRITEBYTECODE="1",
    )
    env.pop("REVIEWER_CONFIG_FILE", None)
    if stub:
        env["REVIEWER_CODEX_BIN"] = str(STUB)
        env["REVIEWER_PI_BIN"] = str(STUB)
    if codex_recorder is not None:
        env["REVIEWER_CODEX_BIN"] = str(codex_recorder)
    env.update(extra or {})
    return env


def run_hook(script, payload, env, timeout=1800):
    process = subprocess.run(
        [sys.executable, str(ROOT / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    try:
        output = json.loads(process.stdout) if process.stdout.strip() else {}
    except ValueError:
        output = {}
    return process, output


def _is_running(pid):
    """True while `pid` exists. A zombie counts as gone: it has stopped executing."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                            capture_output=True, text=True)
    return not status.stdout.strip().startswith("Z")


def decision(output):
    return (output.get("hookSpecificOutput") or {}).get("permissionDecision")


def commit_payload(repo, session_id):
    return {
        "cwd": str(repo),
        "session_id": session_id,
        "tool_name": "Bash",
        "tool_input": {"command": 'git commit -m "matrix"'},
    }


def transcript(path, text):
    path.write_text(
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
        }) + "\n",
        encoding="utf-8",
    )
    return str(path)


def codex_recorder(directory):
    """A stand-in for the OTHER backends' binaries: records every invocation and refuses to run.

    Used to PROVE a Pi or Claude review never reaches Codex (and, for Claude, never reaches Pi
    either). A recorder that merely logged would let a silent fallback succeed and go unnoticed;
    refusing makes any fallback loud.
    """
    log = directory / "codex-invocations.log"
    script = directory / "codex-recorder.py"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "open({!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "sys.stderr.write('this backend must not be invoked for this review\\n')\n"
        "sys.exit(97)\n".format(str(log)),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script, log


def _model_diagnostic_check(result, repo, verdict_env):
    """A model the provider will refuse must produce a diagnostic naming the control."""
    bogus = verdict_env(
        stub_output="", stub_exit=1,
        stub_stderr="The model `definitely-not-a-real-model-xyz` does not exist or you do not "
                    "have access to it.",
        REVIEWER_CODEX_MODEL="definitely-not-a-real-model-xyz",
    )
    stage(repo, "diag.py", FIXED)
    _process, output = run_hook("commit_hook.py", commit_payload(repo, "matrix-diag"), bogus,
                                timeout=600)
    reason = (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    result.check(
        "an unusable model is blocked with an actionable diagnostic",
        decision(output) == "deny" and "reviewerModel" in reason,
        reason[:300],
    )


# --------------------------------------------------------------------------
# scenario 1 and 2: the Claude Code hooks, once per backend
# --------------------------------------------------------------------------
def claude_scenario(workspace, backend, stub):
    result = Result("claude-{}".format(backend))
    if backend in ("pi", "claude") and stub:
        # The stand-in speaks the codex argv/stdout shape, and neither of the other two backends
        # reads it. Pi's verdict is parsed out of its JSON event stream and the gate converts "no
        # successful git_read for the staged patch" into a rejection; Claude's is parsed out of a
        # `--output-format json` result entry. A stubbed run of either would report a fail-closed
        # rejection for every case and prove nothing about that backend. Saying so beats a green
        # tick. (`tests/test_claude_backend.py` covers the Claude gate against its own stub; what
        # only a live run can establish is that the real CLI still behaves as measured.)
        result.skipped = "{} cannot be stubbed; run without --stub".format(backend)
        return result
    state = workspace / "state"
    repo_a = make_repo(workspace / "repo-a", "repo-a")
    repo_b = make_repo(workspace / "repo-b", "repo-b")
    recorder = log = None
    extra = {"REVIEWER_BACKEND": backend}
    if backend != "codex" and not stub:
        recorder, log = codex_recorder(workspace)
        if backend == "claude":
            # Claude must reach NEITHER of the others, so pi's binary is the same tripwire.
            extra["REVIEWER_PI_BIN"] = str(recorder)
    env = hook_env(state, extra, stub=stub, codex_recorder=recorder)

    # enable BOTH repositories, exactly as `/reviewerAgent on acceptRisk` does -- the plain `on`
    # refuses without a receipt, so the accept token is required to actually gate the repo (the
    # receipt it records is per-repository root and is shared with stopContinue below). Both
    # repositories, because the cross-repository check below is meaningless if the second one is
    # never gated.
    for repo in (repo_a, repo_b):
        toggle = subprocess.run(
            [sys.executable, str(ROOT / "toggle.py"), "on", "acceptRisk"],
            capture_output=True, text=True, cwd=str(repo),
            env=dict(env, CLAUDE_PROJECT_DIR=str(repo)),
        )
        result.check("reviewer enabled for {}".format(repo.name), toggle.returncode == 0,
                     toggle.stderr[-400:])

    def verdict_env(stub_output=None, stub_exit=None, stub_stderr=None, **overrides):
        """Env for one call. In stub mode the stand-in is TOLD what verdict to give, so both
        branches of the gate are exercised; in live mode the model decides, which is the whole
        point of running this live."""
        call_env = dict(env, **overrides)
        if stub:
            call_env["FAKE_CODEX_STDOUT"] = stub_output or ""
            # The streaming codex commit audit reads its verdict from FAKE_CODEX_VERDICT (written
            # to the -o schema file), not from stdout, so the stub must be told which verdict to
            # give. Derive it from the same string the caller already passes: anything that begins
            # "REJECTED" is a rejection, everything else (including "APPROVED") an approval.
            # FAKE_CODEX_FINDINGS is left to the fixture's default (one item when REJECTED).
            call_env["FAKE_CODEX_VERDICT"] = (
                "REJECTED" if (stub_output or "").strip().upper().startswith("REJECTED")
                else "APPROVED"
            )
            if stub_exit is not None:
                call_env["FAKE_CODEX_EXIT"] = str(stub_exit)
            if stub_stderr is not None:
                call_env["FAKE_CODEX_STDERR"] = stub_stderr
        return call_env

    # 1. a seeded defect is rejected
    stage(repo_a, "helper.py", DEFECT)
    process, output = run_hook(
        "commit_hook.py", commit_payload(repo_a, "matrix-session"),
        verdict_env("REJECTED:\n1. average() divides by zero on an empty sequence."),
    )
    reason = (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason", "")
    result.check(
        "defective staged change is rejected",
        decision(output) == "deny" and "REJECTED" in reason.upper(),
        reason[:300] or process.stderr[-300:],
    )

    # 2. its correction is approved. Under the gate-owns-the-commit contract the gate performs the
    #    commit ITSELF, then rewrites the agent's `git commit` to a harmless echo and ALLOWS the call
    #    (updatedInput) so it succeeds without re-committing. Assert the allow + the rewritten
    #    command, and -- the point of the design -- that the reviewed set actually landed: HEAD
    #    advanced and the committed file is the FIXED content.
    parent = _git(repo_a, "rev-parse", "HEAD").stdout.strip()
    stage(repo_a, "helper.py", FIXED)
    process, output = run_hook(
        "commit_hook.py", commit_payload(repo_a, "matrix-session"), verdict_env("APPROVED"),
    )
    hso = output.get("hookSpecificOutput") or {}
    rewritten = (hso.get("updatedInput") or {}).get("command", "")
    head = _git(repo_a, "rev-parse", "HEAD").stdout.strip()
    committed = _git(repo_a, "show", "HEAD:helper.py", check=False).stdout
    subject = _git(repo_a, "log", "-1", "--format=%s").stdout.strip()
    result.check(
        "corrected change is approved, committed by the gate, and the call is allowed (rewritten)",
        decision(output) == "allow"
        and rewritten.startswith("echo ") and "git commit" not in rewritten
        and head != parent
        and committed == FIXED
        and subject == "matrix",
        "decision={} rewritten={!r} head_moved={} subject={!r}".format(
            decision(output), rewritten[:80], head != parent, subject,
        ),
    )

    # 3. the same host session in a second repository gets its own reviewer
    stage(repo_b, "other.py", FIXED)
    run_hook("commit_hook.py", commit_payload(repo_b, "matrix-session"),
             verdict_env("APPROVED"))
    sessions = sorted(p.name for p in (state / "state").glob("*.sess_matrix-session.*"))
    stems = {name.split(".sess_", 1)[0] for name in sessions}
    result.check(
        "two repositories, two reviewer state stems",
        len(stems) >= 2,
        ", ".join(sorted(stems))[:300],
    )

    # 4. stop supervision: an obviously unfinished turn is continued
    stop_env = dict(env, CLAUDE_PROJECT_DIR=str(repo_a))
    subprocess.run(
        [sys.executable, str(ROOT / "stopcontinue_toggle.py"), "on"],
        capture_output=True, text=True, cwd=str(repo_a), env=stop_env,
    )
    incomplete = transcript(
        workspace / "incomplete.jsonl",
        "I have written the first of the three functions you asked for. Shall I continue?",
    )
    process, output = run_hook("stop_hook.py", {
        "cwd": str(repo_a),
        "session_id": "matrix-session",
        "transcript_path": incomplete,
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }, verdict_env("Continue: finish the remaining two functions."))
    result.check(
        "an unfinished stop is blocked with a directive",
        (output.get("decision") == "block") or ("stopContinue" in json.dumps(output)),
        json.dumps(output)[:300] or process.stderr[-300:],
    )

    # 5. cancellation kills the review and records the outcome
    if not stub:
        # A review big enough to still be running when we cancel it. A fixed sleep is not
        # enough on its own: a small diff can be reviewed inside it, and then the "cancellation"
        # cancels nothing and the checks report on an event that never happened.
        bulky = DEFECT.replace("average", "mean") + "\n".join(
            "def helper_{0}(values):\n    return sum(values) / len(values)  # defect {0}\n"
            .format(index) for index in range(300)
        )
        stage(repo_a, "slow.py", bulky)
        cancel = subprocess.Popen(
            [sys.executable, str(ROOT / "commit_hook.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=verdict_env(), start_new_session=True,
        )
        cancel.stdin.write(json.dumps(commit_payload(repo_a, "matrix-cancel")))
        cancel.stdin.close()
        # Poll for the reviewer to actually exist, rather than assuming it does after N seconds.
        # Snapshot the hook's OWN descendant tree: matching on a command line cannot work for
        # both backends -- codex carries `-C <repo>` in argv, while Pi is given the repository
        # as its working directory and names it nowhere -- so a leaked Pi reviewer would go
        # unnoticed by any pattern match. Process identity is backend-agnostic, and cannot
        # mistake the operator's unrelated sessions for ours.
        launched = []
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            launched = [pid for pid, _group in _descendants(cancel.pid)]
            if launched:
                break
            if cancel.poll() is not None:
                break
            time.sleep(0.2)
        # Settle before signalling. The guard process becomes a descendant the instant Popen
        # returns, a few statements before the hook installs its cancellation handler, so
        # signalling the moment a descendant appears can land in that window -- the reviewer
        # dies, but the outcome goes unrecorded, and the harness reports a product defect that
        # is really a race in the harness.
        time.sleep(5)
        cancel.send_signal(signal.SIGTERM)
        try:
            cancel.wait(timeout=60)
        except subprocess.TimeoutExpired:
            cancel.kill()
        deadline = time.monotonic() + 15
        alive = launched
        while time.monotonic() < deadline and alive:
            alive = [pid for pid in alive if _is_running(pid)]
            if alive:
                time.sleep(0.2)
        survivors = ", ".join(str(pid) for pid in alive)
        result.check("the cancelled review had started a reviewer at all", bool(launched),
                     "no descendant processes were observed before cancelling")
        history = list((state / "reviews").glob("*.history/*.txt"))
        cancelled = any("OUTCOME: CANCELLED" in item.read_text(encoding="utf-8")
                        for item in history)
        result.check("cancelling records the outcome", cancelled,
                     "{} history entries".format(len(history)))
        result.check(
            "no reviewer process survived the cancellation",
            not survivors,
            survivors[:200],
        )

    # 6. a shell-free backend never falls back to another one
    if log is not None:
        result.check(
            "no other backend's executable was invoked for a {} review".format(backend),
            not log.exists() or not log.read_text(encoding="utf-8").strip(),
            log.read_text(encoding="utf-8")[:300] if log.exists() else "",
        )

    # 6b. claude only: the reviewer must have no shell. A live run is the only place this can be
    # established, because it is the real CLI's permission engine that enforces it, not this code.
    if backend == "claude" and not stub:
        stage(repo_a, "shellcheck.py", FIXED)
        _process, output = run_hook(
            "commit_hook.py", commit_payload(repo_a, "matrix-claude-tools"), verdict_env(),
        )
        # The executed argv is recorded per audit, not in reviewer.log: `_run` captures what it
        # actually ran, so this reads the real command line rather than rebuilding it here --
        # a rebuilt copy is exactly what would keep passing after the guard flags were dropped.
        argvs = "".join(
            item.read_text(encoding="utf-8")
            for item in (state / "reviews").glob("*.audits.jsonl")
        )
        result.check(
            "the claude reviewer ran with safe mode and a fail-closed permission mode",
            "--safe-mode" in argvs and "dontAsk" in argvs,
            "no audit record carries the argv guard flags",
        )
        result.check(
            "a claude review still returns a verdict",
            decision(output) in ("allow", "deny"),
            json.dumps(output)[:300],
        )

    # 7. a refused model produces an actionable diagnostic. Live: a genuinely unusable model
    # name, so the provider's own refusal is what gets classified. Stub: the same refusal text
    # replayed, so the classification is exercised without a network call.
    #
    # CODEX ONLY. Under the pi backend the reviewer binary is the recorder that proves Codex is
    # never invoked, and forcing REVIEWER_BACKEND=codex here would invoke it -- failing the
    # no-Codex assertion with the harness's own doing. The diagnostic itself is backend-agnostic
    # and is covered by the claude-codex scenario.
    if backend == "codex":
        _model_diagnostic_check(result, repo_b, verdict_env)
    # 8. disabling leaves the repository ungated
    subprocess.run(
        [sys.executable, str(ROOT / "toggle.py"), "off"],
        capture_output=True, text=True, cwd=str(repo_a),
        env=dict(env, CLAUDE_PROJECT_DIR=str(repo_a)),
    )
    stage(repo_a, "after-off.py", DEFECT)
    process, output = run_hook("commit_hook.py", commit_payload(repo_a, "matrix-session"),
                               verdict_env("APPROVED"))
    result.check("disabling stops the gate", output == {}, json.dumps(output)[:200])
    return result


# --------------------------------------------------------------------------
# scenario 3: the native Codex MCP tool
# --------------------------------------------------------------------------
class McpClient:
    """The smallest JSON-RPC client that can drive the plugin's stdio MCP server.

    Lines are read by a background thread into a queue rather than by calling `readline()` in
    the request loop. A blocking `readline()` cannot be interrupted, so a server that answers
    nothing would hang the whole matrix past any deadline the loop believed it was enforcing --
    the timeout would be decoration.
    """

    def __init__(self, env):
        self.process = subprocess.Popen(
            [sys.executable, str(CODEX_PLUGIN / "scripts" / "mcp_server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=str(CODEX_PLUGIN),
        )
        self._id = 0
        self._lines = queue.Queue()
        self._reader = threading.Thread(target=self._read_lines, daemon=True)
        self._reader.start()

    def _read_lines(self):
        for line in self.process.stdout:
            self._lines.put(line)
        self._lines.put(None)  # EOF sentinel

    def call(self, method, params=None, timeout=3600):
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            message["params"] = params
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.close()
                raise TimeoutError("{} did not answer within {}s".format(method, timeout))
            try:
                line = self._lines.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("mcp server closed: " + self.process.stderr.read()[-400:])
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            if payload.get("id") == self._id:
                return payload

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()


def codex_scenario(workspace, stub):
    result = Result("codex-native")
    if stub:
        # The native path reads its verdict from `--output-last-message`, a file the real codex
        # CLI writes and the stand-in does not. Every review would surface as "empty verdict".
        result.skipped = "the native Codex tool cannot be stubbed; run without --stub"
        return result
    state = workspace / "codex-state"
    repo = make_repo(workspace / "codex-repo", "codex-repo")
    env = dict(os.environ, REVIEWER_CODEX_STATE_DIR=str(state), PYTHONDONTWRITEBYTECODE="1")
    if stub:
        env["REVIEWER_CODEX_BIN"] = str(STUB)

    sys.path.insert(0, str(CODEX_PLUGIN))
    from reviewer_codex import state as codex_state  # noqa: E402

    # The imported module reads REVIEWER_CODEX_STATE_DIR from THIS process's environment, so it
    # has to be set -- and restored, or every later scenario in the same run inherits a state
    # directory belonging to this one, which is exactly the isolation this harness promises.
    previous_state_dir = os.environ.get("REVIEWER_CODEX_STATE_DIR")
    os.environ["REVIEWER_CODEX_STATE_DIR"] = str(state)
    try:
        return _codex_scenario_body(result, repo, workspace, state, env, codex_state)
    finally:
        if previous_state_dir is None:
            os.environ.pop("REVIEWER_CODEX_STATE_DIR", None)
        else:
            os.environ["REVIEWER_CODEX_STATE_DIR"] = previous_state_dir


def _codex_scenario_body(result, repo, workspace, state, env, codex_state):
    codex_state.set_feature(repo, "reviewer")
    result.check("reviewer enabled for the repository",
                 codex_state.feature_enabled(repo, "reviewer"))

    client = McpClient(env)
    try:
        initialized = client.call("initialize", {"protocolVersion": "2024-11-05"}, timeout=60)
        version = ((initialized.get("result") or {}).get("serverInfo") or {}).get("version")
        result.check("the MCP server advertises the release version",
                     version == release_versions.RELEASE, str(version))

        listed = client.call("tools/list", {}, timeout=60)
        names = {tool["name"] for tool in (listed.get("result") or {}).get("tools", [])}
        result.check("commit review and lifecycle-only completion tools are offered",
                     names == {"review_commit", "stop_hook", "interrupt_stop"},
                     ", ".join(sorted(names)))

        stage(repo, "helper.py", DEFECT)
        token = codex_state.create_commit_ticket(repo, "matrix-codex-session")
        response = client.call("tools/call", {
            "name": "review_commit",
            "arguments": {"cwd": str(repo), "message": "matrix", "review_token": token},
        })
        text = json.dumps(response)
        result.check("review_commit rejects the seeded defect",
                     "rejected" in text.lower(), text[:300])

        stage(repo, "helper.py", FIXED)
        token = codex_state.create_commit_ticket(repo, "matrix-codex-session")
        response = client.call("tools/call", {
            "name": "review_commit",
            "arguments": {"cwd": str(repo), "message": "matrix fixed", "review_token": token},
        })
        text = json.dumps(response)
        committed = "committed" in text.lower()
        result.check("review_commit approves and commits the correction", committed, text[:300])
        if committed:
            subject = _git(repo, "log", "-1", "--format=%s").stdout.strip()
            result.check("the commit really landed", subject == "matrix fixed", subject)
    finally:
        client.close()

    # a fresh process sees the same project-scoped state: this is what "survives a restart" means
    reviewer = codex_state.read_reviewer_session(repo, "matrix-codex-session", "reviewer")
    result.check("the reviewer thread is recorded per project and session", bool(reviewer),
                 str(reviewer))
    other = make_repo(workspace / "codex-repo-b", "codex-repo-b")
    result.check(
        "a second repository shares no reviewer thread",
        codex_state.read_reviewer_session(other, "matrix-codex-session", "reviewer") is None,
    )
    result.check("verdict history was retained", bool(codex_state.review_history(repo)),
                 str(len(codex_state.review_history(repo))))
    return result


# --------------------------------------------------------------------------
# scenario 4: distribution -- the suites, from a clean export rather than the checkout
# --------------------------------------------------------------------------
def distribution_scenario(workspace, stub):
    result = Result("distribution")
    sys.path.insert(0, str(ROOT / "tools"))
    import release_export as rex  # noqa: E402
    import scan_release as scanner  # noqa: E402

    before = _git(ROOT, "status", "--porcelain").stdout

    export = workspace / "export"
    rex.export(export)
    result.check("the export verifies", rex.verify(export) == [], str(rex.verify(export)))
    result.check("the export is clean", scanner.scan(export, check_commits=False) == [])

    # Initialise the export as a repository with one root commit, which is exactly what the
    # cutover does before anything is verified. Without this the release checks -- which files
    # are tracked, what the export excludes -- skip for want of a working tree, and skipping
    # them here would leave the release's own verification unverified.
    subprocess.run(["git", "init", "-q", str(export)], check=True)
    _git(export, "config", "user.email", "matrix@example.com")
    _git(export, "config", "user.name", "Release Matrix")
    # `core.excludesFile=/dev/null` is load-bearing, and this scenario is how that was found: a
    # plain `git add -A` here tracked 187 of 189 files, silently dropping BOTH `.gitignore`
    # files, because the maintainer's GLOBAL excludes file ignores that name. The public
    # repository would then have shipped with no ignore rules at all, so every contributor's
    # `__pycache__` -- and worse, any `state/`, `logs/` or `reviews/` runtime directory -- would
    # appear as untracked rather than ignored. The repository's own `.gitignore` still applies.
    _git(export, "-c", "core.excludesFile=/dev/null", "add", "-A")
    _git(export, "commit", "-qm", "initial public release")
    tracked = _git(export, "ls-files").stdout.split()
    missing = sorted(set(rex.included_files()) - set(tracked))
    result.check("the root commit tracks the whole export", not missing,
                 "missing: " + ", ".join(missing[:5]))
    result.check(
        "both .gitignore files survived the root commit",
        ".gitignore" in tracked and "plugins/reviewer-agent-codex/.gitignore" in tracked,
    )
    result.check(
        "the root commit has no parent",
        not _git(export, "log", "--format=%P", "-1").stdout.strip(),
    )

    # A pristine environment for the export suites: every REVIEWER_* variable is stripped, so
    # they run as a fresh clone would rather than inheriting a state directory, a backend
    # selection or a stubbed binary from an earlier scenario -- any of which would make this
    # "clean archive" check quietly depend on what ran before it.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("REVIEWER_")
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    root_suite = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        capture_output=True, text=True, cwd=str(export), env=env, timeout=3600,
    )
    result.check("the root suite passes from the export", root_suite.returncode == 0,
                 root_suite.stdout[-400:])
    nested = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        capture_output=True, text=True,
        cwd=str(export / "plugins" / "reviewer-agent-codex"), env=env, timeout=3600,
    )
    result.check("the nested suite passes from the export", nested.returncode == 0,
                 nested.stdout[-400:])

    archive = workspace / "export.tar.gz"
    rex.write_archive(export, archive)
    again = rex.write_archive(export, workspace / "export-again.tar.gz")
    result.check("the archive is reproducible",
                 Path(archive).read_bytes() == Path(again).read_bytes())
    # UNCHANGED, not clean: this runs inside a working checkout that legitimately has work in
    # progress. What matters is that the matrix itself wrote nothing into it.
    after = _git(ROOT, "status", "--porcelain").stdout
    result.check("the matrix left the checkout untouched", after == before,
                 "".join(sorted(set(after.splitlines()) ^ set(before.splitlines())))[:300])
    return result


SCENARIOS = {
    "claude-codex": lambda workspace, stub: claude_scenario(workspace, "codex", stub),
    "claude-pi": lambda workspace, stub: claude_scenario(workspace, "pi", stub),
    "claude-claude": lambda workspace, stub: claude_scenario(workspace, "claude", stub),
    "codex-native": codex_scenario,
    "distribution": distribution_scenario,
}

HUMAN_CHECKLIST = """
Still requires a person, in a real host, and cannot be scripted here:

  1. Press Escape during a live commit review in Claude Code, and confirm the TUI returns
     control immediately and the next commit still works.
  2. Restart Codex and confirm the plugin is still enabled for the repository, then run
     `/hooks` and confirm the hooks are trusted.
  3. Install into a genuinely clean host profile (a fresh macOS account) following
     `INSTALL_WITH_AGENT.md`, and again following the manual instructions.
  4. Confirm shell-commit redirection in native Codex: a bare `git commit` is denied with the
     absolute-path retry instruction.
  5. Confirm the `AskUserQuestion` reroute in a real interactive Claude Code session with
     `stopContinue` on: the tool is denied (no dialog) and the agent re-asks in prose.
  6. Uninstall on each host and confirm unrelated plugins, marketplaces and settings survive.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", action="append", choices=sorted(SCENARIOS))
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--stub", action="store_true",
                        help="use the deterministic test stand-in instead of a real reviewer")
    parser.add_argument("--report", help="write a Markdown report here")
    parser.add_argument("--keep", action="store_true", help="keep the temporary workspace")
    args = parser.parse_args()

    if args.list or not (args.scenario or args.all):
        print("scenarios:")
        for name in sorted(SCENARIOS):
            print("  " + name)
        print(HUMAN_CHECKLIST)
        return 0

    chosen = sorted(SCENARIOS) if args.all else args.scenario
    workspace = Path(tempfile.mkdtemp(prefix="reviewer-matrix-"))
    results = []
    try:
        for name in chosen:
            print("== {} ({})".format(name, "stub" if args.stub else "live"), flush=True)
            scenario_workspace = workspace / name
            scenario_workspace.mkdir(parents=True, exist_ok=True)
            try:
                result = SCENARIOS[name](scenario_workspace, args.stub)
            except Exception as error:  # a harness failure is a matrix failure, and is reported
                result = Result(name)
                result.check("scenario ran to completion", False, "{}: {}".format(
                    type(error).__name__, error))
            results.append(result)
            if result.skipped:
                print("   SKIP {}".format(result.skipped), flush=True)
            for description, passed, detail in result.checks:
                print("   {} {}{}".format("PASS" if passed else "FAIL", description,
                                          "" if passed else "  <- " + detail.replace("\n", " ")),
                      flush=True)
    finally:
        if not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            print("workspace kept at {}".format(workspace))

    lines = ["# Release matrix", "",
             "Mode: {}".format("stub (no model calls)" if args.stub else "live"), ""]
    for result in results:
        status = "SKIPPED" if result.skipped else ("PASS" if result.ok else "FAIL")
        lines.append("## {} -- {}".format(result.name, status))
        if result.skipped:
            lines.append("- skipped: {}".format(result.skipped))
        for description, passed, detail in result.checks:
            lines.append("- [{}] {}{}".format("x" if passed else " ", description,
                                              "" if passed else " -- " + detail[:200]))
        lines.append("")
    lines.append(HUMAN_CHECKLIST)
    report = "\n".join(lines)
    if args.report:
        Path(args.report).write_text(report + "\n", encoding="utf-8")
        print("report written to {}".format(args.report))
    failed = [result.name for result in results if not result.ok]
    skipped = [result.name for result in results if result.skipped]
    print("\n{} scenario(s) run, {} failed, {} skipped".format(
        len(results), len(failed), len(skipped)))
    if skipped:
        print("SKIPPED: {} -- this run does not qualify a release".format(", ".join(skipped)))
    # A skipped scenario exits NON-ZERO, deliberately. Nothing here should be able to report
    # overall success while a backend went unexercised: an exit code of 0 from this harness is
    # the claim that the matrix ran, and a green tick covering a skip is how an unexercised
    # backend reaches a release.
    return 1 if (failed or skipped) else 0


if __name__ == "__main__":
    raise SystemExit(main())
