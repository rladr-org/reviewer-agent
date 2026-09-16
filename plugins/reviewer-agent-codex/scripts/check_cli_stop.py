#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Opt-in live CLI check. Uses real model calls and existing plugin enablement/hook trust.

Records documented CLI output and MCP lifecycle messages, never Codex rollout files.
No saved configuration, feature flags, or installed plugin files are changed.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time


INCOMPLETE = "I have not calculated it yet."
FINAL = "17 × 19 = 323."


def record(kind, message):
    return {"kind": kind, "at": time.monotonic_ns(), "message": message}


class Observer:
    """Pass MCP bytes through unchanged; retain only completion and Stop exchanges."""
    def __init__(self, source, destination, append):
        self.source, self.destination, self.append = source, destination, append
        self.pending, self.buffer = set(), ""

    def input(self):
        for line in self.source:
            message = json.loads(line)
            if (message.get("method") == "tools/call"
                    and message.get("params", {}).get("name") in ("review_completion", "stop_hook")):
                self.pending.add(message["id"])
                self.append(record("mcp_request", message))
            yield line

    def write(self, text):
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            message = json.loads(line)
            if message.get("id") in self.pending:
                self.pending.remove(message["id"])
                self.append(record("mcp_response", message))
        return self.destination.write(text)

    def flush(self):
        self.destination.flush()


def proxy(plugin_root, events):
    sys.path.insert(0, str(plugin_root))
    from reviewer_codex import mcp_server
    lock = threading.Lock()

    def append(item):
        with lock, events.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")

    observer = Observer(sys.stdin, sys.stdout, append)
    mcp_server.serve(input_stream=observer.input(), output_stream=observer)


def assess(records, scenario, returncode):
    """Require actual host-hook evidence, not merely a plausible final answer."""
    def require(condition, message):
        if not condition:
            raise ValueError(message)

    require(returncode == 0, "CLI failed with exit code {}".format(returncode))
    cli = [r["message"] for r in records if r["kind"] == "cli"]
    threads = [r["thread_id"] for r in cli if r.get("type") == "thread.started"]
    require(len(threads) == 1, "missing or ambiguous CLI thread")
    require(any(r.get("type") == "turn.completed" for r in cli), "CLI did not complete")
    require(not any(r.get("type") in ("error", "turn.failed") for r in cli), "CLI reported an error")
    items = [r["item"] for r in cli if r.get("type") == "item.completed"]
    messages = [r.get("text") for r in items if r.get("type") == "agent_message"]
    require(messages and messages[-1] == FINAL, "missing correct final response")
    require(not any(r.get("type") == "mcp_tool_call" and r.get("tool") == "stop_hook"
                    for r in items), "agent called native Stop manually")
    requests = [r["message"] for r in records if r["kind"] == "mcp_request"]
    response_records = [r for r in records if r["kind"] == "mcp_response"]
    responses = {r["message"]["id"]: r["message"] for r in response_records}
    response_times = {r["message"]["id"]: r["at"] for r in response_records}
    require(len(responses) == len(response_records) == len(requests)
            == len({r["id"] for r in requests}), "missing or duplicate MCP exchanges")
    completion, stops = [], []
    approved_at = None
    for request in requests:
        response = responses.get(request["id"], {})
        require("error" not in response, "MCP error")
        result = response.get("result", {})
        require(not result.get("isError"), "MCP tool error")
        content = result.get("structuredContent")
        require(isinstance(content, dict), "missing structured hook/tool result")
        args = request["params"]["arguments"]
        if request["params"]["name"] == "review_completion":
            completion.append((args.get("proposed_response"), content.get("status")))
            if content.get("status") == "stop_ok":
                approved_at = response_times[request["id"]]
        else:
            require(args.get("session_id") == threads[0], "Stop belongs to another thread")
            stops.append((args.get("last_assistant_message"), content))
    if scenario == "prefinal":
        require(completion == [(INCOMPLETE, "incomplete"), (FINAL, "stop_ok")],
                "expected rejected draft then approved draft; fail-open is not approval")
        require(stops == [(FINAL, {})], "expected one silent native Stop after pre-final approval")
        require(INCOMPLETE not in messages, "pre-final rejection was published")
        published_at = [r["at"] for r in records if r["kind"] == "cli"
                        and r["message"].get("type") == "item.completed"
                        and r["message"].get("item", {}).get("type") == "agent_message"
                        and r["message"]["item"].get("text") == FINAL]
        require(approved_at is not None and approved_at < min(published_at),
                "final response was emitted before pre-final approval")
    else:
        require(not completion, "fallback unexpectedly used pre-final review")
        require(len(stops) == 2 and stops[0][0] == INCOMPLETE
                and stops[0][1].get("decision") == "block" and stops[0][1].get("reason"),
                "native Stop did not reject the incomplete response")
        require(stops[1] == (FINAL, {}), "native Stop did not allow the corrected response")
    return {"scenario": scenario, "rejected_response_emitted": INCOMPLETE in messages,
            "completion_reviews": len(completion), "native_stops": len(stops)}


def terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except ProcessLookupError:
        process.wait(timeout=5)


def run_scenario(args, scenario, directory):
    directory.mkdir()
    events_path = directory / "mcp.jsonl"
    cli_path = directory / "cli.jsonl"
    proxy_args = [str(Path(__file__).resolve()), "--proxy", "--plugin-root", str(args.plugin_root),
                  "--events", str(events_path)]
    command = [args.codex, "exec", "--sandbox", "read-only", "-C", str(args.cwd), "--json",
               "-c", "mcp_servers.reviewer-agent.command=" + json.dumps(sys.executable),
               "-c", "mcp_servers.reviewer-agent.args=" + json.dumps(proxy_args)]
    common = ("Controlled CLI integration test. Do not edit files, invoke skills, run shell commands, "
              "or call native lifecycle tools manually. The substantive task is to calculate 17 times 19. ")
    if scenario == "prefinal":
        prompt = (common + 'First call review_completion with proposed_response exactly "' + INCOMPLETE
                  + '" before doing the calculation. After rejection, calculate the result and review "'
                  + FINAL + '". Publish only the approved final draft. The first draft is intentional '
                  'test input, not completion of the substantive task. Follow the host review protocol.')
    else:
        command += ["-c", 'mcp_servers.reviewer-agent.disabled_tools=["review_completion"]']
        prompt = (common + 'The pre-final tool is intentionally unavailable. First attempt to end with '
                  'exactly "' + INCOMPLETE + '". If the native Stop hook returns a missing-work directive, '
                  'calculate and report exactly "' + FINAL + '". The incomplete first draft is intentional '
                  'test input, not completion of the substantive task. Use the native Stop fallback.')
    command.append(prompt)
    errors = []
    with (directory / "stderr.txt").open("w", encoding="utf-8") as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=stderr, text=True, start_new_session=True)

        def collect():
            try:
                with cli_path.open("w", encoding="utf-8") as stream:
                    for line in process.stdout:
                        item = record("cli", json.loads(line))
                        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                        stream.flush()
            except Exception as error:
                errors.append(str(error))

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        try:
            process.wait(timeout=args.timeout)
        finally:
            terminate(process)
            reader.join(timeout=10)
            if not reader.is_alive():
                process.stdout.close()
    if errors or reader.is_alive():
        raise ValueError("cannot read CLI output: {}".format(errors or "reader did not exit"))
    records = []
    for path in (cli_path, events_path):
        if path.exists():
            records.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return assess(records, scenario, process.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Authorize real CLI and reviewer model calls")
    parser.add_argument("--cwd", type=Path, help="Repository with stopContinue already enabled")
    parser.add_argument("--plugin-root", type=Path, help="Installed, trusted reviewer-agent-codex directory")
    parser.add_argument("--codex", default=os.environ.get("REVIEWER_CODEX_BIN", "codex"))
    parser.add_argument("--scenario", choices=("all", "prefinal", "fallback"), default="all")
    parser.add_argument("--timeout", type=int, default=180, help="Maximum seconds per scenario")
    parser.add_argument("--proxy", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--events", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.proxy:
        if not args.plugin_root or not args.events:
            parser.error("proxy needs --plugin-root and --events")
        proxy(args.plugin_root.resolve(), args.events)
        return 0
    if not args.run:
        parser.error("--run is required; this check consumes real model usage")
    if not args.cwd or not args.plugin_root:
        parser.error("--cwd and --plugin-root are required")
    if os.name != "posix" or not 10 <= args.timeout <= 900:
        parser.error("requires a POSIX host and --timeout between 10 and 900 seconds")
    args.cwd, args.plugin_root = args.cwd.resolve(), args.plugin_root.resolve()
    sys.path.insert(0, str(args.plugin_root))
    from reviewer_codex import state
    if not state.feature_enabled(args.cwd, "stop"):
        parser.error("stopContinue must already be enabled for --cwd; this check does not enable it")
    manifest = json.loads((args.plugin_root / ".codex-plugin" / "plugin.json").read_text())
    version = subprocess.run([args.codex, "--version"], capture_output=True, text=True,
                             check=True, timeout=10).stdout.strip()
    root = Path(tempfile.mkdtemp(prefix="cli-stop-check-"))
    print("Artifacts: " + str(root), flush=True)
    summary = {"codex": version, "plugin_version": manifest["version"],
               "plugin_root": str(args.plugin_root), "results": []}
    scenarios = ("prefinal", "fallback") if args.scenario == "all" else (args.scenario,)
    try:
        for scenario in scenarios:
            print("Running " + scenario, flush=True)
            summary["results"].append(run_scenario(args, scenario, root / scenario))
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        summary["error"] = str(error) or "interrupted"
        print("FAIL: " + summary["error"], file=sys.stderr)
        return 1
    finally:
        (root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("PASS: " + json.dumps(summary["results"]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
