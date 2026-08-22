# SPDX-License-Identifier: MPL-2.0
"""Codex-native liveness tracking from tool and subagent lifecycle events."""

import json
import os
import shutil
import time
import uuid
from pathlib import Path

from .state import project_token, state_home


def _safe(value):
    import hashlib
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _session_dir(path, session_id):
    """`liveness/<session>/<project>/`, or None outside a Git repository.

    Project-scoped for the same reason the reviewer thread is: a Codex session can move between
    repositories, and background work started in one is not work the other's stop supervisor
    should wait on. The session level nests OUTSIDE the project level so ending a session
    removes every project's markers in one tree delete.
    """
    token = project_token(path)
    if not session_id or not token:
        return None
    return state_home() / "liveness" / _safe(session_id) / token


def _item_path(path, session_id, kind, item_id):
    directory = _session_dir(path, session_id)
    return None if directory is None else directory / (kind + "-" + _safe(item_id) + ".json")


def _challenge_path(path, session_id):
    directory = _session_dir(path, session_id)
    return None if directory is None else directory / "_challenge.json"


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value), encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        temporary.unlink(missing_ok=True)


def mark(project_path, session_id, kind, item_id, label=""):
    if not session_id or not item_id:
        return
    path = _item_path(project_path, session_id, kind, item_id)
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp-" + str(os.getpid()))
    temporary.write_text(
        json.dumps({"kind": kind, "id": str(item_id), "label": str(label), "time": time.time()}),
        encoding="utf-8",
    )
    os.replace(str(temporary), str(path))


def clear(project_path, session_id, kind, item_id):
    if session_id and item_id:
        path = _item_path(project_path, session_id, kind, item_id)
        if path is not None:
            path.unlink(missing_ok=True)


def clear_session(session_id):
    """Drop every project's markers for one ended session, in one tree delete."""
    if not session_id:
        return
    directory = state_home() / "liveness" / _safe(session_id)
    if directory.is_dir():
        shutil.rmtree(str(directory), ignore_errors=True)


def _item_keys(items, default_kind=None):
    keys = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or default_kind or "").strip()
        item_id = str(item.get("id") or "").strip()
        if kind in {"shell", "subagent"} and item_id:
            keys.add((kind, item_id))
    return keys


def _shell_ids(items):
    return {
        item_id
        for kind, item_id in _item_keys(items, default_kind="shell")
        if kind == "shell"
    }


def challenge_items(project_path, session_id, items):
    """Record the background marker set after one wait/inspection nudge."""
    if not session_id:
        return
    path = _challenge_path(project_path, session_id)
    if path is None:
        return
    keys = sorted(_item_keys(items))
    if not keys:
        clear_challenge(project_path, session_id)
        return
    _atomic_json(
        path,
        {
            "items": [{"kind": kind, "id": item_id} for kind, item_id in keys],
            "time": time.time(),
        },
    )


def _challenged_keys(value):
    keys = _item_keys(value.get("items", []))
    # Read the shell-only format written by releases before mixed-marker recovery.
    keys.update(
        ("shell", str(item))
        for item in value.get("ids", [])
        if str(item).strip()
    )
    return keys


def items_were_challenged(project_path, session_id, items):
    """Return true when no current marker is newer than the prior challenge."""
    current = _item_keys(items)
    if not session_id or not current:
        return False
    try:
        path = _challenge_path(project_path, session_id)
        value = json.loads(path.read_text(encoding="utf-8"))
        challenged = _challenged_keys(value)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return current.issubset(challenged)


def challenge_shells(project_path, session_id, items):
    """Record the shell marker set for which one wait/inspection nudge was issued."""
    challenge_items(
        project_path,
        session_id,
        [
            {"kind": "shell", "id": item_id}
            for item_id in sorted(_shell_ids(items))
        ],
    )


def shells_were_challenged(project_path, session_id, items):
    """Return true when no current shell marker is newer than the prior challenge."""
    shell_items = [
        {"kind": "shell", "id": item_id}
        for item_id in sorted(_shell_ids(items))
    ]
    return items_were_challenged(project_path, session_id, shell_items)


def clear_challenge(project_path, session_id):
    if session_id:
        path = _challenge_path(project_path, session_id)
        if path is not None:
            path.unlink(missing_ok=True)


def clear_shell_items(project_path, session_id, items):
    """Clear only the exact shell markers adjudicated by stopContinue."""
    clear_items(
        project_path,
        session_id,
        [
            {"kind": "shell", "id": item_id}
            for item_id in sorted(_shell_ids(items))
        ],
    )


def clear_items(project_path, session_id, items):
    """Clear only the exact background markers adjudicated by stopContinue."""
    for kind, item_id in _item_keys(items):
        clear(project_path, session_id, kind, item_id)
    clear_challenge(project_path, session_id)


def _ttl_seconds():
    try:
        return max(60, int(os.environ.get("REVIEWER_LIVENESS_TTL_SECONDS", "86400")))
    except ValueError:
        return 86400


def outstanding(project_path, session_id):
    directory = _session_dir(project_path, session_id)
    if directory is None:
        return []
    try:
        paths = [path for path in directory.glob("*.json") if path.name != "_challenge.json"]
    except OSError:
        return []
    now, live = time.time(), []
    for path in paths:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            timestamp = float(item.get("time", 0))
        except (OSError, ValueError, TypeError):
            path.unlink(missing_ok=True)
            continue
        if now - timestamp > _ttl_seconds():
            path.unlink(missing_ok=True)
            continue
        live.append(item)
    return sorted(live, key=lambda item: (item.get("kind", ""), item.get("time", 0)))


def describe(items, limit=8):
    lines = []
    shown = list(items or [])[: max(0, int(limit))]
    for item in shown:
        kind = item.get("kind")
        label = str(item.get("label") or "").strip()
        if kind == "shell":
            lines.append("shell command" + (": " + label[:180] if label else ""))
        elif kind == "subagent":
            lines.append("subagent" + (" (" + label[:80] + ")" if label else ""))
        else:
            lines.append(str(kind or "background work"))
    omitted = len(items or []) - len(shown)
    if omitted > 0:
        lines.append("... and {} more background items".format(omitted))
    return lines
