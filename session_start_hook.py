#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""SessionStart hook for reviewerAgent.

When the reviewer is active for a Codex-backed project, brief a fresh Codex reviewer
session and capture its session id, persisting it for THIS Claude session. The hooks then
resume that session BY ID. Pi-backed projects skip SessionStart: Pi creates and captures its
session on the first gated action, so selecting Pi never launches or requires Codex.

Briefing here (rather than lazily on the first commit) means the reviewer session
is established at session start, exactly as intended. Inactive projects do nothing.
Failure is logged but never blocks the session (the PreToolUse hooks lazily brief as
a fallback).

SEPARATELY -- and independent of the main reviewer flag -- when stopContinueWatchdog is on
for the project, this hook injects `additionalContext` telling the fresh/resumed session to
re-arm its CronCreate recovery cron (the backstop for the `/stopContinueWatchdog on`-turn
arming). See docs/plans/stopcontinue_watchdog.md.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reviewer_lib as rl  # noqa: E402


# A plugin running from a temporary directory loses its files to the OS's cleanup, and then every
# hook exits 127 -- non-blocking, so the commit gate fails open (2026-10-07). Say so while the
# files still exist. See docs/plans/temp_plugin_root_warning.md.
def default_temp_dirs():
    return ["/tmp", "/var/tmp", "/var/folders", tempfile.gettempdir()]


def temp_plugin_root(root, temp_dirs=None):
    """The temporary directory that `root` lies under, or None. Both sides are compared after
    realpath, so /tmp and /private/tmp match alike, and by path components, so /tmpfoo is not
    under /tmp."""
    real = os.path.realpath(root)
    for temp in default_temp_dirs() if temp_dirs is None else temp_dirs:
        base = os.path.realpath(temp)
        if base not in ("", os.sep) and (real == base or real.startswith(base.rstrip(os.sep) + os.sep)):
            return base
    return None


def temp_root_warning(root):
    return ("reviewer-agent is running from a temporary directory ({}). The OS can delete it, and "
            "then every reviewer-agent hook fails open, the commit gate included. Reinstall from "
            "a permanent location (AGENT_UPDATE.md, \"Testing an unreleased build without a "
            "version bump\").".format(root))


def _brief_if_active(data, cwd):
    """Establish/capture the reviewer session at session start (Codex backend only). Pure
    side effects + logging; never prints and never raises out."""
    if not rl.is_active(cwd):  # resolves cwd -> canonical project root internally
        return
    claude_sid = rl.claude_session_key(data)
    key = rl.canonical_project_key(cwd)
    backend = rl.reviewer_backend(cwd)
    if backend in ("pi", "claude"):
        rl.log(
            "[session_start] key={} claude_sid={} backend={} -> lazy first gated action".format(
                key, rl.short(str(claude_sid), 60), backend
            )
        )
        return
    sid, serr = rl.ensure_session(cwd, claude_sid)
    if sid:
        rl.log(
            "[session_start] key={} claude_sid={} codex_sid={} -> reviewer ready".format(
                key, rl.short(str(claude_sid), 60), sid
            )
        )
    else:
        rl.log(
            "[session_start] key={} claude_sid={} brief FAILED: {}".format(
                key, rl.short(str(claude_sid), 60), serr
            )
        )


def _watchdog_rearm_context(cwd, claude_sid):
    """The SessionStart additionalContext that re-arms the watchdog cron, or None. Gated on the
    EFFECTIVE state (watchdog flag AND stopContinue active), so a session that resumes after
    stopContinue was turned off does not re-arm ticks the now-inert Stop hook cannot supervise.
    Independent of the main reviewer flag -- the watchdog is its own opt-in. `claude_sid` is baked
    into the tick's session-scoped teardown_ready marker path."""
    if not rl.is_stopcontinue_watchdog_effective(cwd):
        return None
    return rl.watchdog_arm_instruction(cwd, rl.watchdog_interval(cwd), claude_sid)


def main():
    # One guard over the whole body: an escaped exception exits 1, which Claude Code reports as
    # a hook error on every session start. SessionStart blocks nothing, so it fails open.
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        cwd = data.get("cwd") or rl.project_dir()
        # Reclaim abandoned reviewer scratch on EVERY session start -- unconditional, so it also
        # runs for inactive, Pi and Claude sessions (unlike the codex-only brief below). Liveness
        # is the mtime lease, so a live session's dir is within the TTL and kept. Never blocks the
        # session.
        try:
            rl.sweep_orphan_scratch_dirs()
        except Exception as e:
            rl.log("[session_start] scratch sweep EXCEPTION {}: {}".format(type(e).__name__, e))
        try:
            _brief_if_active(data, cwd)
        except Exception as e:
            rl.log("[session_start] brief EXCEPTION {}: {}".format(type(e).__name__, e))
        out = {}
        ctx = _watchdog_rearm_context(cwd, rl.claude_session_key(data))
        if ctx:
            rl.log("[session_start] key={} watchdog re-arm context injected".format(
                rl.canonical_project_key(cwd)))
            out.update(rl.sessionstart_context_output(ctx))
        try:
            root = str(Path(__file__).resolve().parent)
            if temp_plugin_root(root) is not None:
                rl.log("[session_start] plugin root {} is under a temporary directory -> "
                       "warned".format(root))
                out["systemMessage"] = temp_root_warning(root)
        except Exception as e:
            rl.log("[session_start] temp-root check EXCEPTION {}: {}".format(type(e).__name__, e))
        if out:
            print(json.dumps(out))
        return 0
    except Exception as e:
        try:
            rl.log("[session_start] EXCEPTION {}: {}".format(type(e).__name__, e))
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
