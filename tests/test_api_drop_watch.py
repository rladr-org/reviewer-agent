# SPDX-License-Identifier: MPL-2.0
"""The API-drop watcher: a background command that resumes a turn which ended on a dropped API
connection, within seconds instead of at the next watchdog tick. See
docs/plans/watchdog_api_drop_watcher.md.

Observed 2026-10-24 to 26: 51 "Connection lost mid-response", 16 "The response stopped arriving"
and 2 "No response from API" turns on one machine, each idle until the next tick (up to ~10
minutes), because Claude Code runs no Stop hook after them and does not retry them itself.
"""
import datetime
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import reviewer_lib as rl
import reviewer_manifest as m
from test_background_wait_crossturn import _at
from test_background_wait_server import _ack, _bg_shell
from test_reviewer_lib import _asst, _bg_agent, _human, _job_state, _transcript
from test_stop_hook import FAKE, _input, make_repo, run_hook

_BASE = Path(__file__).resolve().parent.parent
WATCH = str(_BASE / "api_drop_watch.py")
UPS = str(_BASE / "userpromptsubmit_hook.py")

LOST = "API Error: Connection lost mid-response. The response above may be incomplete."
STOPPED = "API Error: The response stopped arriving. The response above may be incomplete."
NO_RESPONSE = ("API Error: No response from API (waited 3m, then 10m on the retry). If a proxy "
               "or gateway on your network holds responses until they complete, raise "
               "API_TIMEOUT_MS or CLAUDE_STREAM_FIRST_BYTE_TIMEOUT_MS to wait longer.")
HTTP_500 = ("API Error: 500 Internal server error. This is a server-side issue, usually "
            "temporary — try again in a moment.")
OVERLOADED = ("API Error: 529 Overloaded. This is a server-side issue, usually temporary — try "
              "again in a moment.")


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    for var in ("REVIEWER_STATE_DIR", "REVIEWER_BACKEND", "CLAUDE_CODE_SESSION_ID",
                "CLAUDE_SESSION_ID", "REVIEWER_API_DROP_WATCH", "REVIEWER_API_DROP_GRACE_SECS",
                "REVIEWER_API_DROP_POLL_SECS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))


def _drop(text=LOST, error="server_error", uuid="err-1", **extra):
    ev = {"type": "assistant", "uuid": uuid, "isApiErrorMessage": True, "error": error,
          "message": {"role": "assistant", "model": "<synthetic>",
                      "content": [{"type": "text", "text": text}]}}
    ev.update(extra)
    return ev


def _tool_result(tool_use_id="toolu_X", text="The server-side auto mode classifier gave no "
                 "verdict (the response ended early)."):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}


def _tick():
    return {"type": "user", "isMeta": True, "message": {"role": "user", "content": (
        "stopContinueWatchdog tick -- Check these three paths INDIVIDUALLY")}}


def _notification():
    return {"type": "user", "message": {"role": "user", "content": (
        "<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
        "</task-notification>")}}


def _enqueue(text="continue"):
    return {"type": "queue-operation", "operation": "enqueue", "content": text}


def _turn(*after):
    return [_human("fix the allergen table"), _asst("Editing the table now."), *after]


# --- detection: which error entry is a drop still waiting for a resume --------------------------

@pytest.mark.parametrize("text", [LOST, STOPPED, NO_RESPONSE, HTTP_500, OVERLOADED])
def test_every_server_error_that_ends_a_turn_is_a_pending_drop(text):
    drop = rl.api_drop_pending(_turn(_drop(text)))
    assert drop is not None
    assert drop["uuid"] == "err-1"
    assert drop["text"] == text


@pytest.mark.parametrize("error,text", [
    ("authentication_failed", "Login expired · Please run /login"),
    ("rate_limit", "You've hit your session limit · resets 1:40am (America/Los_Angeles)"),
    ("invalid_request", "API Error: Fable 5's safeguards flagged this message"),
])
def test_errors_a_retry_cannot_fix_are_not_drops(error, text):
    assert rl.api_drop_pending(_turn(_drop(text, error=error))) is None


@pytest.mark.parametrize("after", [
    {"type": "api-request"},
    _asst("Picking up where I left off."),
    _human("continue"),
    _tick(),
    _notification(),
    _enqueue(),
], ids=["api-request", "assistant", "prompt", "tick", "task-notification", "enqueue"])
def test_anything_showing_the_session_moved_on_cancels_the_drop(after):
    assert rl.api_drop_pending(_turn(_drop(), after)) is None


# Claude Code's own pre-output retry loop ("Waiting for API response · will retry in 2m 1s ·
# check your network") writes no error entry while it waits: no saved transcript on the machine
# this was built on has one. What the transcript shows is the request, and one more per attempt
# (observed 2026-10-27: requests at 05:00:39 and 05:03:57, the 3-minute first-byte wait and its
# retry). Only when the loop gives up does an error entry appear.

def _retry_loop(*before):
    return [*before, _human("fix the table"), _asst("Editing."), _tool_result(),
            {"type": "api-request"}, {"type": "api-request-blob"}, {"type": "api-request"}]


def test_claude_codes_own_retry_loop_is_not_a_drop():
    assert rl.api_drop_pending(_retry_loop()) is None


def test_a_retry_loop_after_an_earlier_resumed_drop_is_not_a_drop():
    assert rl.api_drop_pending(_retry_loop(_human("go"), _drop(uuid="old"))) is None


@pytest.mark.parametrize("text", [
    "API Error: Unable to connect to API (ENOTFOUND). A VPN, firewall, or security software can "
    "cause this. Check those or switch networks, then try again.",
    "API Error: Connection to the API was lost (ECONNRESET). This is usually temporary — try "
    "again.",
], ids=["unable-to-connect", "connection-lost-code"])
def test_a_retry_loop_that_gives_up_is_a_drop(text):
    assert rl.api_drop_pending(_retry_loop() + [_drop(text)]) is not None


@pytest.mark.parametrize("api_error", ["tls_untrusted_ca", "gateway_content_type"])
def test_a_connection_failure_from_configuration_is_not_a_drop(api_error):
    # The same connection-failure path tags these server_error too, but a proxy's untrusted
    # certificate or a gateway's wrong content type fails the same way on every retry.
    ev = _drop("API Error: Unable to connect to API (SELF_SIGNED_CERT_IN_CHAIN).",
               apiError=api_error)
    assert rl.api_drop_pending(_turn(ev)) is None


def test_a_classifier_failure_that_carries_on_is_cancelled_by_its_next_request():
    # The auto-mode classifier's own API call failed: same error entry, then a tool result, then
    # the turn carries on with a new request. That turn is alive.
    events = _turn(_drop(), _tool_result(), {"type": "api-request-blob"}, {"type": "api-request"})
    assert rl.api_drop_pending(events) is None


@pytest.mark.parametrize("after", [
    _tool_result(),
    {"type": "system", "subtype": "turn_duration"},
    {"type": "last-prompt"},
    {"type": "ai-title"},
    {"type": "attachment", "attachment": {"type": "total_tokens_reminder"}},
    {"type": "file-history-snapshot"},
], ids=["tool-result", "turn-duration", "last-prompt", "ai-title", "attachment", "snapshot"])
def test_bookkeeping_after_the_drop_does_not_cancel_it(after):
    assert rl.api_drop_pending(_turn(_drop(), after)) is not None


def test_a_sub_agent_error_is_not_a_drop_of_the_session():
    assert rl.api_drop_pending(_turn(_drop(isSidechain=True))) is None


def test_sub_agent_activity_after_the_drop_postpones_it_until_quiet():
    # A sub-agent is not the main turn, so its events do not cancel the drop; but something is
    # still active, so the grace runs from its LATEST event, not from the drop.
    side = _asst("sub-agent still talking")
    side.update(isSidechain=True, timestamp="2026-10-27T05:01:30Z")
    drop = rl.api_drop_pending(_turn(_drop(timestamp="2026-10-27T05:00:00Z"), side))
    assert drop is not None
    assert drop["quiet_since"] == pytest.approx(datetime.datetime(
        2026, 10, 27, 5, 1, 30, tzinfo=datetime.timezone.utc).timestamp())


def test_without_later_activity_the_grace_runs_from_the_drop():
    drop = rl.api_drop_pending(_turn(_drop(timestamp="2026-10-27T05:00:00Z")))
    assert drop["quiet_since"] == pytest.approx(datetime.datetime(
        2026, 10, 27, 5, 0, 0, tzinfo=datetime.timezone.utc).timestamp())


def test_a_drop_already_resumed_is_not_pending_even_with_a_later_tool_result():
    events = _turn(_drop(uuid="old"), _human("continue"), _asst("Resumed."), _tool_result())
    assert rl.api_drop_pending(events) is None


def test_the_drop_it_already_fired_for_is_not_pending_again():
    assert rl.api_drop_pending(_turn(_drop(uuid="err-1")), fired_uuid="err-1") is None
    assert rl.api_drop_pending(_turn(_drop(uuid="err-2")), fired_uuid="err-1") is not None


# --- when to fire: the grace, and backing off during an outage ----------------------------------

def _watch_module():
    return importlib.import_module("api_drop_watch")


def test_a_drop_fires_once_it_is_older_than_the_grace():
    w = _watch_module()
    now = 10_000.0
    assert w.fire_due(now - 5, now, fires=[], grace=20) is False
    assert w.fire_due(now - 20, now, fires=[], grace=20) is True


def test_three_fires_in_ten_minutes_stretch_the_grace_to_five_minutes():
    w = _watch_module()
    now = 10_000.0
    recent = [now - 500, now - 300, now - 60]
    assert w.fire_due(now - 60, now, fires=recent, grace=20) is False
    assert w.fire_due(now - 300, now, fires=recent, grace=20) is True
    old = [now - 1_000, now - 900, now - 700]
    assert w.fire_due(now - 20, now, fires=old, grace=20) is True


def _boom(*_a, **_k):
    raise RuntimeError("detector failure")


def test_the_strict_background_check_raises_where_the_stop_hook_form_says_no_wait(
        tmp_path, monkeypatch):
    path = _transcript(tmp_path, _human("go"), _drop())
    monkeypatch.setattr(m, "_background_shell_launches", _boom)
    assert m.background_wait_state(path) == (False, [])
    with pytest.raises(RuntimeError):
        m.background_wait_state(path, strict=True)


def test_an_undecidable_background_check_keeps_the_watcher_waiting(tmp_path, monkeypatch):
    w = _watch_module()
    path = _transcript(tmp_path, _human("go"), _drop())
    assert w.background_work_outstanding(path) is False
    monkeypatch.setattr(m, "_background_shell_launches", _boom)
    assert w.background_work_outstanding(path) is True


# --- the lease: exactly one watcher per session -------------------------------------------------

def _dead_pid():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _write_lease(repo, sid, pid, seconds_old=0, **extra):
    ts = (datetime.datetime.now(datetime.timezone.utc)
          - datetime.timedelta(seconds=seconds_old)).isoformat()
    f = Path(rl.api_drop_lease_file(repo, sid))
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(dict({"pid": pid, "ts": ts}, **extra)))


def test_the_lease_is_session_scoped_beside_the_other_watchdog_markers(tmp_path):
    repo = make_repo(tmp_path)
    a, b = rl.api_drop_lease_file(repo, "s1"), rl.api_drop_lease_file(repo, "s2")
    assert a != b
    assert a.name.endswith(".dropwatch")
    assert a.parent == rl.watchdog_disarm_file(repo, "s1").parent


def test_a_watcher_is_alive_only_with_a_running_pid_and_a_fresh_heartbeat(tmp_path):
    repo = make_repo(tmp_path)
    assert rl.api_drop_watcher_alive(repo, "s") is False
    _write_lease(repo, "s", os.getpid())
    assert rl.api_drop_watcher_alive(repo, "s") is True
    _write_lease(repo, "s", os.getpid(), seconds_old=120)
    assert rl.api_drop_watcher_alive(repo, "s") is False
    _write_lease(repo, "s", _dead_pid())
    assert rl.api_drop_watcher_alive(repo, "s") is False


def test_a_requested_launch_holds_off_a_second_request_for_a_minute(tmp_path):
    repo = make_repo(tmp_path)
    assert rl.api_drop_launch_pending(repo, "s") is False
    rl.request_api_drop_watcher(repo, "s")
    assert rl.api_drop_launch_pending(repo, "s") is True
    assert rl.api_drop_watcher_alive(repo, "s") is False
    _write_lease(repo, "s", None, seconds_old=120)
    assert rl.api_drop_launch_pending(repo, "s") is False


def test_a_requested_launch_keeps_what_the_last_watcher_recorded(tmp_path):
    repo = make_repo(tmp_path)
    _write_lease(repo, "s", _dead_pid(), fired_uuid="err-9", fires=[1.0, 2.0])
    rl.request_api_drop_watcher(repo, "s")
    lease = json.loads(Path(rl.api_drop_lease_file(repo, "s")).read_text())
    assert lease["pid"] is None
    assert lease["fired_uuid"] == "err-9"
    assert lease["fires"] == [1.0, 2.0]


# --- the text the agent acts on -----------------------------------------------------------------

def test_the_launch_instruction_names_the_command_and_the_background_flag(tmp_path):
    repo = make_repo(tmp_path)
    text = rl.api_drop_watcher_launch_instruction(repo, "s", "/tmp/t.jsonl")
    command = rl.api_drop_watcher_command(repo, "s", "/tmp/t.jsonl")
    assert command in text
    assert "run_in_background" in text
    assert rl.API_DROP_WATCHER_DESCRIPTION in text
    assert "api_drop_watch.py" in command and "run_hook.sh" in command
    assert "/tmp/t.jsonl" in command


def test_the_resume_message_relaunches_first_then_honours_the_pause(tmp_path):
    repo = make_repo(tmp_path)
    drop = {"uuid": "err-1", "text": LOST, "ts": "2026-10-27T05:00:38.340Z"}
    text = rl.api_drop_resume_message(repo, "s", "/tmp/t.jsonl", drop)
    command = rl.api_drop_watcher_command(repo, "s", "/tmp/t.jsonl")
    assert LOST in text
    assert command in text and "run_in_background" in text
    assert str(rl.watchdog_disarm_file(repo, "s")) in text
    assert text.index(command) < text.index(str(rl.watchdog_disarm_file(repo, "s")))
    assert "resume" in text.lower()


# --- the watcher is not background work ---------------------------------------------------------

def _watcher_command(root="/plugin"):
    return ('/bin/sh "{0}/hooks/run_hook.sh" "{0}/api_drop_watch.py" --cwd "/repo" '
            '--session s --transcript "/t.jsonl"'.format(root))


def _watcher_launch(tool_use_id="toolu_W", task_id="bwatch", minutes_ago=30, root="/plugin"):
    command = _watcher_command(root)
    return [
        _bg_shell(tool_use_id, command, rl.API_DROP_WATCHER_DESCRIPTION, minutes_ago + 1),
        _ack(tool_use_id, task_id, "/tmp/tasks/{}.output".format(task_id), minutes_ago),
    ]


def test_a_turn_whose_only_background_shell_is_the_watcher_is_not_a_wait(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("plan the checks"), 3),
        *_watcher_launch(minutes_ago=2),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    assert m.background_wait_state(path) == (False, [])


def test_a_watcher_from_an_earlier_turn_is_not_a_wait(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("start"), 40),
        *_watcher_launch(minutes_ago=30),
        _at(_asst("Started; bwatch is the API-drop watcher."), 29),
        _at(_human("next?"), 3),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    assert m.background_wait_state(path) == (False, [])


def test_a_watcher_in_the_job_state_is_not_a_wait(tmp_path, monkeypatch):
    path = _transcript(
        tmp_path,
        _at(_human("start"), 40),
        *_watcher_launch(minutes_ago=30),
        _at(_human("next?"), 3),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, Path(path).stem, [
        {"id": "bwatch", "kind": "shell", "label": _watcher_command(), "startedAt": 1}]))
    assert m.background_wait_state(path) == (False, [])


def test_a_job_state_watcher_whose_launch_ack_is_gone_is_not_a_wait(tmp_path, monkeypatch):
    # Compaction or a respawn can drop the launch ack; the job state still lists the task, and
    # a shell entry's label is its command.
    path = _transcript(
        tmp_path,
        _at(_human("next?"), 3),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, Path(path).stem, [
        {"id": "bwatch", "kind": "shell", "label": _watcher_command(), "startedAt": 1}]))
    assert m.background_wait_state(path) == (False, [])


def test_a_job_state_shell_that_is_not_the_watcher_is_still_a_wait(tmp_path, monkeypatch):
    path = _transcript(
        tmp_path,
        _at(_human("next?"), 3),
        _at(_asst("The sweep is running."), 1),
    )
    monkeypatch.setenv("CLAUDE_JOB_DIR", _job_state(tmp_path, Path(path).stem, [
        {"id": "bsweep", "kind": "shell", "label": "rg api_drop_watch.py", "startedAt": 1}]))
    assert m.background_wait_state(path)[0] is True


@pytest.mark.parametrize("command", [
    "rg api_drop_watch.py",
    "cat /plugin/api_drop_watch.py",
    _watcher_command() + "; python sweep.py",
    _watcher_command() + " && python sweep.py",
    "python3 /plugin/api_drop_watch.py --cwd /repo --session s --transcript /t.jsonl",
    _watcher_command().replace("--transcript", "--other"),
], ids=["grep", "cat", "then-more", "and-more", "not-through-run_hook", "other-flags"])
def test_a_command_that_only_mentions_the_watcher_is_real_background_work(tmp_path, command):
    path = _transcript(
        tmp_path,
        _at(_human("look into it"), 3),
        _bg_shell("toolu_RG", command, "Look into it", 2),
        _ack("toolu_RG", "brg", "/tmp/tasks/brg.output", 2),
        _at(_asst("Running."), 1),
    )
    assert m.background_wait_state(path)[0] is True


def test_the_generated_command_is_recognized_even_with_spaces_in_its_paths(tmp_path):
    command = rl.api_drop_watcher_command(str(tmp_path / "my repo"), "s",
                                          str(tmp_path / "a dir" / "t.jsonl"))
    assert m._is_api_drop_watcher_command(command)


def test_a_watcher_under_a_long_plugin_path_is_still_recognized(tmp_path):
    root = "/opt/claude/plugins/cache/reviewer-agent/reviewer-agent/" + "x" * 140
    path = _transcript(
        tmp_path,
        _at(_human("plan the checks"), 3),
        *_watcher_launch(minutes_ago=2, root=root),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    assert m.background_wait_state(path) == (False, [])


def test_the_watcher_beside_a_real_job_still_parks_the_stop(tmp_path):
    path = _transcript(
        tmp_path,
        _at(_human("run the sweep"), 5),
        *_watcher_launch(minutes_ago=4),
        _bg_shell("toolu_RUN", "python sweep.py", "Run the long sweep", 3),
        _ack("toolu_RUN", "bsweep", "/tmp/tasks/bsweep.output", 3),
        _at(_asst("The sweep is running."), 1),
    )
    assert m.background_wait_state(path)[0] is True


def test_the_stop_hook_supervises_a_stop_with_only_the_watcher_running(tmp_path):
    home = os.environ["REVIEWER_HOME"]
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    rl.write_session_id(repo, "claude-stop", "sid-stop", role=rl.ROLE_STOP)
    tr = _transcript(
        tmp_path,
        _at(_human("plan the three checks"), 3),
        *_watcher_launch(minutes_ago=2),
        _at(_asst("Shall I plan the three checks?"), 1),
    )
    p = run_hook(_input(repo, tr), {
        "REVIEWER_HOME": home, "REVIEWER_BACKEND": "codex", "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_STDOUT": "You were told to plan the three checks; plan them now.",
        "FAKE_CODEX_EXIT": "0"})
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out["decision"] == "block"
    assert "plan the three checks" in out["reason"]


# --- UserPromptSubmit asks for the watcher ------------------------------------------------------

def _effective(repo):
    rl.set_stopcontinue_active(repo)
    rl.set_stopcontinue_watchdog_active(repo)


def _ups(repo, prompt, transcript, sid="s", **env_extra):
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = repo
    env.update(env_extra)
    stdin = {"cwd": repo, "session_id": sid, "prompt": prompt, "transcript_path": transcript,
             "hook_event_name": "UserPromptSubmit"}
    p = subprocess.run([sys.executable, UPS], input=json.dumps(stdin), capture_output=True,
                       text=True, env=env)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return ""
    return json.loads(p.stdout)["hookSpecificOutput"]["additionalContext"]


def test_a_prompt_asks_for_the_watcher_when_none_is_running(tmp_path):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("hello"))
    ctx = _ups(repo, "fix the table", tr)
    assert rl.api_drop_watcher_command(repo, "s", tr) in ctx
    assert "run_in_background" in ctx


def test_a_second_prompt_does_not_ask_again_while_the_launch_is_pending(tmp_path):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("hello"))
    assert "api_drop_watch.py" in _ups(repo, "first", tr)
    assert "api_drop_watch.py" not in _ups(repo, "second", tr)


def test_no_request_while_a_watcher_is_alive(tmp_path):
    repo = make_repo(tmp_path)
    _effective(repo)
    _write_lease(repo, "s", os.getpid())
    tr = _transcript(tmp_path, _human("hello"))
    assert "api_drop_watch.py" not in _ups(repo, "fix the table", tr)


def test_no_request_on_a_tick(tmp_path):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("hello"))
    assert "api_drop_watch.py" not in _ups(
        repo, "stopContinueWatchdog tick -- Check these three paths INDIVIDUALLY", tr)


def test_no_request_with_the_watchdog_off(tmp_path):
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, _human("hello"))
    assert "api_drop_watch.py" not in _ups(repo, "fix the table", tr)


def test_no_request_when_turned_off_by_environment(tmp_path):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("hello"))
    assert "api_drop_watch.py" not in _ups(repo, "fix the table", tr,
                                           REVIEWER_API_DROP_WATCH="0")


# --- the script, end to end ---------------------------------------------------------------------

def _start_watch(repo, transcript, sid="s"):
    env = dict(os.environ)
    env["REVIEWER_API_DROP_GRACE_SECS"] = "0.5"
    env["REVIEWER_API_DROP_POLL_SECS"] = "0.1"
    return subprocess.Popen(
        [sys.executable, WATCH, "--cwd", repo, "--session", sid, "--transcript", transcript],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)


def _append(path, *events):
    with open(path, "a") as fh:
        for ev in events:
            fh.write(json.dumps(ev) + "\n")


def _still_running(proc, seconds):
    try:
        proc.wait(timeout=seconds)
        return False
    except subprocess.TimeoutExpired:
        return True


@pytest.fixture
def watchers():
    started = []
    yield started
    for p in started:
        if p.poll() is None:
            p.kill()
        p.communicate(timeout=10)


def _wait_for_lease_pid(repo, pid, sid="s", timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        f = Path(rl.api_drop_lease_file(repo, sid))
        if f.exists():
            try:
                if json.loads(f.read_text()).get("pid") == pid:
                    return
            except ValueError:
                pass
        time.sleep(0.05)
    raise AssertionError("watcher never took the lease")


def test_the_watcher_exits_with_the_resume_message_after_a_drop(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("fix the table"), _asst("Editing."))
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    _wait_for_lease_pid(repo, proc.pid)
    assert _still_running(proc, 1.0), "exited before any drop"
    _append(tr, _drop(uuid="err-7", timestamp=datetime.datetime.now(
        datetime.timezone.utc).isoformat()))
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert LOST in out
    assert rl.api_drop_watcher_command(repo, "s", tr) in out
    lease = json.loads(Path(rl.api_drop_lease_file(repo, "s")).read_text())
    assert lease["fired_uuid"] == "err-7"
    assert len(lease["fires"]) == 1


def test_the_watcher_keeps_waiting_while_the_turn_carries_on(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _human("fix the table"), _drop(), _tool_result(),
                     {"type": "api-request"})
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_the_watcher_keeps_waiting_through_claude_codes_retry_loop(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, *_retry_loop())
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_the_watcher_does_not_fire_while_the_operator_has_the_turn(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    rl.set_watchdog_disarm(repo, "s")
    tr = _transcript(tmp_path, _human("fix the table"), _drop())
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_the_watcher_does_not_fire_with_the_watchdog_off(tmp_path, watchers):
    repo = make_repo(tmp_path)
    rl.set_stopcontinue_active(repo)
    tr = _transcript(tmp_path, _human("fix the table"), _drop())
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_the_watcher_does_not_fire_while_a_background_sub_agent_is_running(tmp_path, watchers):
    # The sub-agent's completion will start a turn by itself, and it can sit inside a long tool
    # call without writing anything, so quiet after the drop does not mean it ended.
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _at(_human("research the sources"), 3),
                     _at(_bg_agent("toolu_AG"), 2), _at(_drop(), 1))
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_the_watcher_fires_beside_nothing_but_itself(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    tr = _transcript(tmp_path, _at(_human("fix the table"), 3), *_watcher_launch(minutes_ago=2),
                     _at(_drop(), 1))
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert LOST in out


def test_a_relaunched_watcher_does_not_fire_again_for_the_same_drop(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    _write_lease(repo, "s", _dead_pid(), fired_uuid="err-1", fires=[time.time()])
    tr = _transcript(tmp_path, _human("fix the table"), _drop(uuid="err-1"))
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    assert _still_running(proc, 2.0)


def test_a_second_watcher_exits_at_once_and_leaves_the_lease_alone(tmp_path, watchers):
    repo = make_repo(tmp_path)
    _effective(repo)
    _write_lease(repo, "s", os.getpid())
    before = Path(rl.api_drop_lease_file(repo, "s")).read_text()
    tr = _transcript(tmp_path, _human("fix the table"))
    proc = _start_watch(repo, tr)
    watchers.append(proc)
    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert "already running" in out.lower()
    assert Path(rl.api_drop_lease_file(repo, "s")).read_text() == before
