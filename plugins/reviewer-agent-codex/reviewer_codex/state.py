# SPDX-License-Identifier: MPL-2.0
"""Persistent flags, reviewer sessions, prompt context, and nudge budgets."""

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Codex desktop currently runs this on POSIX.
    fcntl = None


DEFAULT_REVIEWER_MODEL = "gpt-5.6-luna"
DEFAULT_REVIEWER_EFFORT = "xhigh"
DEFAULT_COMMIT_REVIEW_TIMEOUT = 3600
DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT = 3
REVIEW_HISTORY_KEEP = 100
MIN_COMMIT_REVIEW_TIMEOUT = 60
MAX_COMMIT_REVIEW_TIMEOUT = 7200
REVIEWER_EFFORTS = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)
_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")


def state_home():
    configured = os.environ.get("REVIEWER_CODEX_STATE_DIR")
    return Path(configured) if configured else Path.home() / ".codex" / "reviewer-agent-codex"


def config_file():
    return state_home() / "config.json"


def load_config():
    try:
        value = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _setting_file(name):
    return state_home() / "settings" / name


def _read_setting(name):
    try:
        value = _setting_file(name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def is_valid_reviewer_model(value):
    return isinstance(value, str) and bool(_MODEL_RE.fullmatch(value.strip()))


def is_valid_reviewer_effort(value):
    return isinstance(value, str) and value.strip() in REVIEWER_EFFORTS


def reviewer_model_detail():
    for source, candidate in (
        ("env REVIEWER_CODEX_MODEL", os.environ.get("REVIEWER_CODEX_MODEL")),
        ("command override", _read_setting("model")),
        ("config.json model", load_config().get("model")),
        ("default", DEFAULT_REVIEWER_MODEL),
    ):
        value = str(candidate or "").strip()
        if is_valid_reviewer_model(value):
            return value, source
    return DEFAULT_REVIEWER_MODEL, "default"


def reviewer_effort_detail():
    for source, candidate in (
        ("env REVIEWER_CODEX_EFFORT", os.environ.get("REVIEWER_CODEX_EFFORT")),
        ("command override", _read_setting("effort")),
        ("config.json reasoningEffort", load_config().get("reasoningEffort")),
        ("default", DEFAULT_REVIEWER_EFFORT),
    ):
        value = str(candidate or "").strip()
        if is_valid_reviewer_effort(value):
            return value, source
    return DEFAULT_REVIEWER_EFFORT, "default"


def reviewer_model():
    return reviewer_model_detail()[0]


def reviewer_effort():
    return reviewer_effort_detail()[0]


def _valid_commit_review_timeout(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if str(parsed) != str(value).strip():
        return None
    if MIN_COMMIT_REVIEW_TIMEOUT <= parsed <= MAX_COMMIT_REVIEW_TIMEOUT:
        return parsed
    return None


def commit_review_timeout_detail():
    for source, candidate in (
        (
            "env REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS",
            os.environ.get("REVIEWER_CODEX_COMMIT_TIMEOUT_SECONDS"),
        ),
        (
            "config.json commitReviewTimeoutSeconds",
            load_config().get("commitReviewTimeoutSeconds"),
        ),
        ("default", DEFAULT_COMMIT_REVIEW_TIMEOUT),
    ):
        value = _valid_commit_review_timeout(candidate)
        if value is not None:
            return value, source
    return DEFAULT_COMMIT_REVIEW_TIMEOUT, "default"


def commit_review_timeout():
    return commit_review_timeout_detail()[0]


def _valid_reviewer_session_audit_limit(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if str(parsed) != str(value).strip():
        return None
    return parsed if 0 <= parsed <= 1000 else None


def reviewer_session_audit_limit_detail():
    """Completed non-retry audits per child session; zero disables rotation."""
    for source, candidate in (
        (
            "env REVIEWER_CODEX_SESSION_AUDIT_LIMIT",
            os.environ.get("REVIEWER_CODEX_SESSION_AUDIT_LIMIT"),
        ),
        ("command override", _read_setting("session_audit_limit")),
        (
            "config.json reviewerSessionAuditLimit",
            load_config().get("reviewerSessionAuditLimit"),
        ),
        ("default", DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT),
    ):
        value = _valid_reviewer_session_audit_limit(candidate)
        if value is not None:
            return value, source
    return DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT, "default"


def reviewer_session_audit_limit():
    return reviewer_session_audit_limit_detail()[0]


def set_reviewer_session_audit_limit(value):
    parsed = _valid_reviewer_session_audit_limit(value)
    if parsed is None:
        raise ValueError("invalid reviewer session audit limit")
    _atomic_write(_setting_file("session_audit_limit"), str(parsed) + "\n")


def clear_reviewer_session_audit_limit():
    _setting_file("session_audit_limit").unlink(missing_ok=True)


def set_reviewer_model(value):
    if not is_valid_reviewer_model(value):
        raise ValueError("invalid reviewer model")
    _atomic_write(_setting_file("model"), value.strip() + "\n")


def set_reviewer_effort(value):
    if not is_valid_reviewer_effort(value):
        raise ValueError("invalid reviewer effort")
    _atomic_write(_setting_file("effort"), value.strip() + "\n")


def clear_reviewer_model():
    _setting_file("model").unlink(missing_ok=True)


def clear_reviewer_effort():
    _setting_file("effort").unlink(missing_ok=True)


# --------------------------------------------------------------------------
# provider rejection of an otherwise well-formed model / effort
#
# is_valid_reviewer_model and is_valid_reviewer_effort check SHAPE, which is all a local check
# can do: reachability is a property of the account and the accepted effort levels are a
# property of the model. Both fail only at call time -- an unreachable model returns 404/403,
# an unsupported level returns HTTP 400 `[reasoning.effort] [invalid_enum_value]`.
#
# Under a fail-CLOSED commit gate that reads as "every commit in this repository is blocked",
# with the provider's sentence buried in a stderr tail. These patterns are deliberately narrow:
# a rate limit or a dropped connection must NOT be reported as a configuration problem.
# --------------------------------------------------------------------------
_REJECTION_PATTERNS = (
    re.compile(r"invalid_enum_value", re.I),
    re.compile(r"reasoning\.effort", re.I),
    re.compile(r"model_not_found", re.I),
    re.compile(r"unknown model", re.I),
    re.compile(r"unsupported model", re.I),
    # Bounded by NEWLINE, not by ".": a model name is full of dots (`gpt-5.6-luna`), and
    # excluding them stopped the commonest provider sentence of all from matching.
    re.compile(r"\bmodel\b[^\n]{0,80}does not exist", re.I),
    # An access refusal only counts when the sentence is ABOUT a model or a reasoning level.
    # "you do not have access to this repository" and "your account does not have access to
    # this organization" are real errors with entirely different remedies, and sending an
    # operator to change model settings for either would be worse than saying nothing.
    re.compile(r"do(es)? not have access[^\n]{0,60}\b(model|reasoning)\b", re.I),
    re.compile(r"\b(model|reasoning)\b[^\n]{0,60}do(es)? not have access", re.I),
    re.compile(r"not authorized[^\n]{0,40}\bmodel\b", re.I),
    # What codex-cli 0.147.0 actually emits for a model an account cannot use, captured from a
    # live run rather than guessed:
    #   ERROR: {"type":"error","status":400,"error":{"type":"invalid_request_error",
    #           "message":"The 'X' model is not supported when using Codex with a ChatGPT
    #           account."}}
    # The earlier patterns matched none of it, so the release matrix reported a bare "review
    # failed (exit 1)" for exactly the case the diagnostic exists to name.
    re.compile(r"\bmodel\b[^\n]{0,120}\bis not supported\b", re.I),
    re.compile(r"invalid_request_error[^\n]{0,200}\bmodel\b", re.I),
)


def model_rejection_detail(text):
    """The provider sentence refusing a model or effort, or None."""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _REJECTION_PATTERNS:
            if pattern.search(stripped):
                return stripped
    return None


def model_rejection_message(text):
    """An actionable diagnostic naming the control, the selection, and where it came from."""
    detail = model_rejection_detail(text)
    model, model_source = reviewer_model_detail()
    effort, effort_source = reviewer_effort_detail()
    return (
        "The reviewer refused the configured selection: {}\n"
        "Model: {} (from {}). Reasoning effort: {} (from {}).\n"
        "This is a selection problem, not a transient failure: the value is well-formed, but "
        "the provider or account will not run it. Choose a reachable model or a level that "
        "model accepts with `$reviewer-agent-codex:reviewer-model`; that control also reports "
        "the current selection and its source. A value coming from an environment variable "
        "wins over the stored setting and has to be changed or unset."
    ).format(detail or "(no provider message)", model, model_source, effort, effort_source)


def _atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(str(temporary), str(path))
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


@contextmanager
def _mutation_lock():
    """Serialize compare-and-mutate state operations across plugin processes."""
    path = state_home() / ".mutation.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _git(path, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def project_root(path):
    root = _git(path, "rev-parse", "--show-toplevel")
    return os.path.realpath(root) if root else None


def project_key(path):
    root = project_root(path)
    if not root:
        return None
    common = _git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return os.path.realpath(common) if common else root


def _project_stem(path):
    key = project_key(path)
    if not key:
        return None
    readable = Path(key).parent.name or "repo"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return readable.replace(" ", "_") + "-" + digest


def _flag(path, feature):
    stem = _project_stem(path)
    return state_home() / "projects" / (stem + "." + feature + ".json") if stem else None


def disclaimer_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def risk_acceptance_file(path):
    stem = _project_stem(path)
    return (
        state_home() / "projects" / (stem + ".risk.accepted.json")
        if stem
        else None
    )


def risk_accepted(path, disclaimer_hash):
    target = risk_acceptance_file(path)
    if target is None:
        return False
    try:
        record = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(
        isinstance(record, dict)
        and record.get("project") == project_key(path)
        and record.get("disclaimer_sha256") == str(disclaimer_hash)
    )


def record_risk_acceptance(path, disclaimer_hash, source):
    root = project_root(path)
    target = risk_acceptance_file(path)
    if not root or target is None:
        raise ValueError("not a git repository")
    _atomic_write(
        target,
        json.dumps(
            {
                "accepted_at_unix": int(time.time()),
                "disclaimer_sha256": str(disclaimer_hash),
                "project": project_key(path),
                "source": str(source),
            },
            sort_keys=True,
        )
        + "\n",
    )
    return target


def _deferred_stop_file(path):
    stem = _project_stem(path)
    return state_home() / "projects" / (stem + ".stop.defer.json") if stem else None


def defer_next_stop(path, turn_id=None):
    if not turn_id:
        return
    target = _deferred_stop_file(path)
    if target is not None:
        _atomic_write(target, json.dumps({"turn_id": str(turn_id)}) + "\n")


def consume_deferred_stop(path, turn_id=None):
    target = _deferred_stop_file(path)
    if target is None:
        return False
    try:
        text = target.read_text(encoding="utf-8")
        target.unlink()
    except OSError:
        return False
    try:
        marker = json.loads(text)
        marker_turn = str(marker.get("turn_id") or "")
    except (ValueError, AttributeError):
        return False
    current_turn = str(turn_id or "")
    return bool(marker_turn and current_turn and marker_turn == current_turn)


def set_feature(path, feature):
    root = project_root(path)
    target = _flag(path, feature)
    if not root or target is None:
        raise ValueError("not a git repository")
    _atomic_write(target, json.dumps({"root": root, "feature": feature}) + "\n")
    return root


def clear_feature(path, feature):
    target = _flag(path, feature)
    if target:
        target.unlink(missing_ok=True)
    if feature == "stop":
        deferred = _deferred_stop_file(path)
        if deferred:
            deferred.unlink(missing_ok=True)


def feature_enabled(path, feature):
    target = _flag(path, feature)
    return bool(target and target.is_file())


def enabled_feature_roots(feature):
    """Return roots recorded as enabled without requiring those repositories as CWD."""
    directory = state_home() / "projects"
    roots = set()
    try:
        paths = directory.glob("*.{}.json".format(feature))
        for path in paths:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                root = str(record.get("root") or "")
            except (OSError, ValueError, AttributeError):
                continue
            if record.get("feature") == feature and Path(root).is_absolute():
                roots.add(root)
    except OSError:
        return []
    return sorted(roots)


def session_key(data):
    value = (data or {}).get("session_id")
    return str(value) if value else None


def _commit_ticket_file(token):
    if not re.fullmatch(r"[a-f0-9]{32}", str(token or "")):
        return None
    return state_home() / "commit-tickets" / (str(token) + ".json")


def _stop_ticket_file(token):
    if not re.fullmatch(r"[a-f0-9]{32}", str(token or "")):
        return None
    return state_home() / "stop-tickets" / (str(token) + ".json")


def create_commit_ticket(path, session_id):
    """Mint a one-use bridge from a shell hook event to the blocking MCP call."""
    root = project_root(path)
    if not root:
        raise ValueError("not a git repository")
    token = uuid.uuid4().hex
    owner = str(session_id or "").strip() or ("unidentified-" + token)
    _atomic_write(
        _commit_ticket_file(token),
        json.dumps({"root": root, "session_id": owner, "created": time.time()}) + "\n",
    )
    return token


def _normalize_background_markers(items, default_kind=None):
    markers, seen = [], set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or default_kind or "").strip()
        item_id = str(item.get("id") or "").strip()
        key = (kind, item_id)
        if kind not in {"shell", "subagent"} or not item_id or key in seen:
            continue
        seen.add(key)
        markers.append(
            {
                "kind": kind,
                "id": item_id,
                "label": str(item.get("label") or "").strip()[:240],
            }
        )
    return markers


def _normalize_shell_markers(items):
    return [
        {"id": item["id"], "label": item["label"]}
        for item in _normalize_background_markers(items, default_kind="shell")
        if item["kind"] == "shell"
    ]


def create_stop_ticket(
    path,
    session_id,
    final_message,
    turn_id=None,
    shell_markers=None,
    background_markers=None,
):
    root = project_root(path)
    if not root or not session_id:
        raise ValueError("an identifiable Git session is required")
    token = uuid.uuid4().hex
    normalized = _normalize_background_markers(background_markers)
    seen = {(item["kind"], item["id"]) for item in normalized}
    for item in _normalize_background_markers(shell_markers, default_kind="shell"):
        key = (item["kind"], item["id"])
        if key not in seen:
            normalized.append(item)
            seen.add(key)
    legacy_shells = [
        {"id": item["id"], "label": item["label"]}
        for item in normalized
        if item["kind"] == "shell"
    ]
    _atomic_write(
        _stop_ticket_file(token),
        json.dumps(
            {
                "root": root,
                "session_id": str(session_id),
                "final_message": str(final_message or ""),
                "turn_id": str(turn_id or ""),
                "operator_generation": stop_generation(root, session_id),
                "background_markers": normalized,
                "shell_markers": legacy_shells,
                "created": time.time(),
            }
        )
        + "\n",
    )
    return token


def _claim_ticket(target, path, max_age):
    root = project_root(path)
    if target is None or not root:
        return None
    claim = target.with_suffix(".claim")
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(str(claim), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (FileExistsError, OSError):
        return None
    os.close(descriptor)
    try:
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or value.get("root") != root:
            return None
        try:
            fresh = 0 <= time.time() - float(value.get("created")) <= max_age
        except (TypeError, ValueError):
            fresh = False
        if not fresh or not str(value.get("session_id") or "").strip():
            return None
        try:
            target.unlink()
        except OSError:
            return None
        return value
    finally:
        try:
            claim.unlink()
        except OSError:
            pass


def claim_commit_ticket(token, path, max_age=3600):
    """Atomically consume a matching ticket; concurrent/repeated claims fail closed."""
    return _claim_ticket(_commit_ticket_file(token), path, max_age)


def claim_stop_ticket(token, path, max_age=3600):
    return _claim_ticket(_stop_ticket_file(token), path, max_age)


def _safe(value):
    text = str(value or "").strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def _stop_permit_file(path, session_id):
    session_token = _safe(session_id)
    project_token = _safe(project_key(path))
    if not session_token or not project_token:
        return None
    return state_home() / "stop-permits" / (
        session_token + "." + project_token + ".permit"
    )


def stop_generation(path, session_id, work=False):
    name = _scoped_name(path, session_id)
    directory = "stop-work-generations" if work else "stop-generations"
    target = state_home() / directory / (name + ".txt") if name else None
    try:
        return target.read_text(encoding="utf-8").strip()
    except (AttributeError, OSError):
        # AttributeError: no scoped name (target is None). OSError (incl. the common
        # missing-file case): any read failure fails open to the empty generation rather
        # than escaping -- callers read this at a pre-review snapshot that is OUTSIDE the
        # supervisor's fail-open guard, so a bare raise here would defeat that contract.
        return ""


def invalidate_stop_permits(path, session_id, work=False):
    """Invalidate approvals after new operator input or observed tool/subagent work.

    Serialized with ``consume_stop_permit`` under the mutation lock: once consume has
    renamed the permit to a claim, the ``target.unlink`` below is a no-op, so the
    generation bump is the only remaining guard against a stale approval -- the bump and
    consume's generation read must not interleave. The permit is unlinked in a ``finally``
    so a failed generation write still revokes the approval; otherwise a caller that
    swallows the error (every hook does) would leave a live permit whose unchanged
    generation still matches and approves.
    """
    name = _scoped_name(path, session_id)
    if not name:
        return
    directory = "stop-work-generations" if work else "stop-generations"
    generation = state_home() / directory / (name + ".txt")
    target = _stop_permit_file(path, session_id)
    with _mutation_lock():
        try:
            _atomic_write(generation, uuid.uuid4().hex)
        except OSError:
            # Removing the permit is not enough: a review already running when this
            # invalidation arrives regrants a fresh permit from its pre-review snapshot
            # (stop_tool._allow_stop -> grant_stop_permit). A generation left stale after a
            # failed bump still equals that snapshot, so consume_stop_permit would approve.
            # Delete the generation instead -- a missing generation reads as "", which no
            # non-empty snapshot equals, so consume fails CLOSED and the stop is re-reviewed.
            generation.unlink(missing_ok=True)
            raise
        finally:
            if target is not None:
                target.unlink(missing_ok=True)


def grant_stop_permit(path, session_id, turn_id=None, final_message="", outcome="stop_ok",
                      operator_generation=None, work_generation=None):
    target = _stop_permit_file(path, session_id)
    if target is not None:
        _atomic_write(target, json.dumps({
            "scope": "completion",
            "turn_id": str(turn_id or ""),
            "message_hash": hashlib.sha256(str(final_message or "").strip().encode()).hexdigest(),
            "operator_generation": (stop_generation(path, session_id)
                                    if operator_generation is None else operator_generation),
            "work_generation": (stop_generation(path, session_id, work=True)
                                if work_generation is None else work_generation),
            "created": time.time(),
            "outcome": outcome,
        }) + "\n")


def consume_stop_permit(path, session_id, turn_id=None, final_message="", max_age=300):
    """Consume one completion handoff while operator input and observed work are unchanged.

    Host continuation turn IDs and final acknowledgments can change after review. The stored
    turn and message hash are provenance, not equality gates. Legacy permits are invalid.
    """
    target = _stop_permit_file(path, session_id)
    if target is None:
        return None
    # The rename + generation compare runs under the mutation lock so it is atomic w.r.t.
    # invalidate_stop_permits: without it, invalidate could bump the generation between
    # this claim's rename and its generation read while its own unlink lands as a no-op,
    # letting a stale permit approve after new work.
    with _mutation_lock():
        claimed = target.with_suffix("." + uuid.uuid4().hex + ".claim")
        try:
            target.rename(claimed)
        except OSError:
            return None
        try:
            value = json.loads(claimed.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                return None
            if value.get("scope") != "completion":
                return None
            if not 0 <= time.time() - float(value.get("created", 0)) <= max_age:
                return None
            if value.get("operator_generation") != stop_generation(path, session_id):
                return None
            if value.get("work_generation") != stop_generation(path, session_id, work=True):
                return None
            outcome = value.get("outcome")
            return outcome if outcome in ("stop_ok", "failed_open") else None
        except (OSError, ValueError, TypeError):
            return None
        finally:
            # The single-use claim is best-effort cleanup; a non-missing unlink error must
            # not escape and turn an already-decided outcome into a bare exception.
            try:
                claimed.unlink()
            except OSError:
                pass


def _stop_abort_bypass_file(path, session_id):
    session_token = _safe(session_id)
    project_token = _safe(project_key(path))
    if not session_token or not project_token:
        return None
    return state_home() / "stop-abort-bypasses" / (
        session_token + "." + project_token + ".json"
    )


def grant_stop_abort_bypass(path, session_id, turn_id):
    """Allow the immediate host-generated Stop after a cancelled MCP turn."""
    target = _stop_abort_bypass_file(path, session_id)
    current_turn = str(turn_id or "").strip()
    if target is not None and current_turn:
        _atomic_write(
            target,
            json.dumps({"turn_id": current_turn, "created": time.time()}) + "\n",
        )


def clear_stop_abort_bypass(path, session_id):
    target = _stop_abort_bypass_file(path, session_id)
    if target is not None:
        target.unlink(missing_ok=True)


def consume_stop_abort_bypass(path, session_id, turn_id, max_age=30):
    """Consume a fresh bypass even if Codex reassigned the interrupted turn ID."""
    target = _stop_abort_bypass_file(path, session_id)
    if target is None:
        return False
    claim = target.with_name(target.name + "." + uuid.uuid4().hex + ".claim")
    try:
        target.replace(claim)
    except OSError:
        return False
    try:
        value = json.loads(claim.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return False
        marker_turn = str(value.get("turn_id") or "")
        current_turn = str(turn_id or "")
        age = time.time() - float(value.get("created"))
        return bool(marker_turn and current_turn and 0 <= age <= max_age)
    except (OSError, TypeError, ValueError):
        return False
    finally:
        claim.unlink(missing_ok=True)


def commit_cursor_file(path, session_id):
    root = project_root(path)
    key = project_key(root) if root else ""
    token = _safe(str(key or "") + "\0" + str(session_id or ""))
    return state_home() / "commit-cursors" / (token + ".json") if token else None


def read_commit_cursor(path, session_id):
    target = commit_cursor_file(path, session_id)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (AttributeError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("files"), dict) else None


def write_commit_cursor(path, session_id, value):
    target = commit_cursor_file(path, session_id)
    if target is not None:
        with _mutation_lock():
            _atomic_write(target, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def clear_commit_cursor(path, session_id, expected_reviewer_id=None):
    """Remove a cursor only when it still belongs to the expected reviewer."""
    target = commit_cursor_file(path, session_id)
    if target is None:
        return False
    with _mutation_lock():
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(value, dict):
            return False
        if expected_reviewer_id is not None and value.get("reviewer_id") != str(
            expected_reviewer_id
        ):
            return False
        try:
            target.unlink()
        except OSError:
            return False
        return True


def commit_retry_file(path, session_id):
    root = project_root(path)
    key = project_key(root) if root else ""
    token = _safe(str(key or "") + "\0" + str(session_id or ""))
    return state_home() / "commit-retries" / (token + ".pending") if token else None


def commit_retry_pending(path, session_id):
    target = commit_retry_file(path, session_id)
    return bool(target and target.exists())


def set_commit_retry_pending(path, session_id):
    target = commit_retry_file(path, session_id)
    if target is not None:
        _atomic_write(target, "pending\n")


def clear_commit_retry_pending(path, session_id):
    target = commit_retry_file(path, session_id)
    if target is not None:
        target.unlink(missing_ok=True)


def project_token(path):
    """A per-repository token for state filenames, or None outside a Git repository.

    Every per-session artifact below carries one. A Codex conversation is not bound to a
    repository -- the operator can `cd` anywhere mid-session -- so keying a reviewer thread, an
    operator-prompt ledger, a plan, a nudge budget or a liveness marker by session alone let one
    repository's supervisor resume another's thread and read its context. `project_key` resolves
    through `--git-common-dir`, so linked worktrees of one repository share the token.

    None outside a repository means NO state, exactly as an unidentifiable session does: a
    single shared slot is what this token exists to prevent.
    """
    return _safe(project_key(path))


def _scoped_name(path, session_id, *parts):
    """`<session>.<project>[.part...]`, or None when either identity is missing.

    The session token leads so `clear_session_artifacts` can find every project's artifacts for
    one ended session with a single prefix match.
    """
    session_token = _safe(session_id)
    token = project_token(path)
    if not session_token or not token:
        return None
    return ".".join((session_token, token) + tuple(str(part) for part in parts))


def _reviewer_session_revision(role):
    # Import lazily: prompts imports state for project and operator context helpers.
    from . import prompts

    return prompts.session_revision(role)


def reviewer_session_file(path, session_id, role):
    name = _scoped_name(path, session_id, role, _reviewer_session_revision(role))
    return state_home() / "sessions" / (name + ".thread") if name else None


def reviewer_session_audits_file(path, session_id, role):
    target = reviewer_session_file(path, session_id, role)
    return target.with_suffix(".audits") if target is not None else None


def read_reviewer_session(path, session_id, role):
    target = reviewer_session_file(path, session_id, role)
    try:
        value = target.read_text(encoding="utf-8").strip()
    except (AttributeError, OSError):
        return None
    return value or None


def publish_reviewer_session(path, session_id, role, reviewer_id):
    target = reviewer_session_file(path, session_id, role)
    if target is None:
        return reviewer_id
    with _mutation_lock():
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return read_reviewer_session(path, session_id, role) or reviewer_id
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(str(reviewer_id).strip() + "\n")
        _atomic_write(reviewer_session_audits_file(path, session_id, role), "0\n")
        return reviewer_id


def reviewer_session_completed_audits(path, session_id, role="reviewer"):
    counter = reviewer_session_audits_file(path, session_id, role)
    if counter is None:
        return 0
    try:
        value = int(counter.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0
    return max(0, value)


def record_completed_reviewer_audit(
    path, session_id, reviewer_id, role="reviewer"
):
    """Count a completed non-retry audit and retire at the configured limit.

    Returns ``(count, retired)``. The caller decides whether the audit was a retry; keeping
    that decision beside prompt construction prevents rejected/corrective rounds from leaking
    into this state primitive.
    """
    target = reviewer_session_file(path, session_id, role)
    counter = reviewer_session_audits_file(path, session_id, role)
    limit = reviewer_session_audit_limit()
    expected = str(reviewer_id or "").strip()
    if target is None or counter is None or not expected or limit == 0:
        return 0, False
    with _mutation_lock():
        try:
            current = target.read_text(encoding="utf-8").strip()
        except OSError:
            return 0, False
        if current != expected:
            return 0, False
        try:
            count = int(counter.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            count = 0
        count = max(0, count) + 1
        if count >= limit:
            target.unlink(missing_ok=True)
            counter.unlink(missing_ok=True)
            return count, True
        _atomic_write(counter, str(count) + "\n")
        return count, False


def retire_reviewer_session(path, session_id, role, expected_reviewer_id=None):
    """Retire exactly one reviewer thread without deleting a concurrent replacement."""
    target = reviewer_session_file(path, session_id, role)
    if target is None:
        return False
    with _mutation_lock():
        try:
            current = target.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if expected_reviewer_id is not None and current != str(
            expected_reviewer_id
        ).strip():
            return False
        try:
            target.unlink()
        except OSError:
            return False
        counter = reviewer_session_audits_file(path, session_id, role)
        if counter is not None:
            counter.unlink(missing_ok=True)
        return True


def prompt_file(path, session_id):
    name = _scoped_name(path, session_id)
    return state_home() / "prompts" / (name + ".jsonl") if name else None


def record_prompt(project_path, session_id, prompt):
    path = prompt_file(project_path, session_id)
    text = str(prompt or "").strip()
    if path is None or not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"prompt": text}, ensure_ascii=False) + "\n")


def recent_prompts(project_path, session_id, char_budget=12000):
    path = prompt_file(project_path, session_id)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (AttributeError, OSError):
        return []
    prompts = []
    for line in lines[-100:]:
        try:
            value = json.loads(line).get("prompt")
        except (AttributeError, ValueError):
            continue
        if isinstance(value, str) and value.strip():
            prompts.append(value.strip())
    kept, total = [], 0
    for prompt in reversed(prompts):
        if kept and total + len(prompt) > char_budget:
            break
        kept.append(prompt)
        total += len(prompt)
    return list(reversed(kept))


def plan_file(path, session_id):
    name = _scoped_name(path, session_id)
    return state_home() / "plans" / (name + ".json") if name else None


def record_plan(project_path, session_id, value):
    path = plan_file(project_path, session_id)
    if path is None or not isinstance(value, dict) or not isinstance(value.get("plan"), list):
        return
    _atomic_write(path, json.dumps(value, ensure_ascii=False) + "\n")


def current_plan(project_path, session_id):
    path = plan_file(project_path, session_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (AttributeError, OSError, ValueError):
        return None
    return value if isinstance(value, dict) and isinstance(value.get("plan"), list) else None


def nudge_file(path, session_id):
    name = _scoped_name(path, session_id)
    return state_home() / "nudges" / (name + ".count") if name else None


def read_nudges(project_path, session_id):
    path = nudge_file(project_path, session_id)
    try:
        return max(0, int(path.read_text(encoding="utf-8").strip()))
    except (AttributeError, OSError, ValueError):
        return 0


def reset_nudges(project_path, session_id):
    path = nudge_file(project_path, session_id)
    if path:
        path.unlink(missing_ok=True)


def bump_nudges(project_path, session_id):
    value = read_nudges(project_path, session_id) + 1
    path = nudge_file(project_path, session_id)
    if path:
        _atomic_write(path, str(value) + "\n")
    return value


def max_nudges():
    try:
        return max(1, int(os.environ.get("REVIEWER_STOP_MAX_NUDGES", "10")))
    except ValueError:
        return 10


def last_review_file(path, session_id):
    stem = _project_stem(path) or "unknown"
    token = _safe(session_id) or "unknown"
    return state_home() / "reviews" / (stem + "." + token + ".txt")


def review_history_keep():
    """Return the per-project verdict cap, resolved at write time."""
    raw = os.environ.get("REVIEWER_CODEX_REVIEW_HISTORY_KEEP")
    if raw is not None:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return REVIEW_HISTORY_KEEP
    try:
        return max(
            1,
            int(load_config().get("reviewHistoryKeep", REVIEW_HISTORY_KEEP)),
        )
    except (TypeError, ValueError):
        return REVIEW_HISTORY_KEEP


def review_history_dir(path):
    stem = _project_stem(path) or "unknown"
    return state_home() / "reviews" / (stem + ".history")


def review_history(path):
    """Return retained verdicts for one project in chronological order."""
    directory = review_history_dir(path)
    try:
        return sorted(item for item in directory.iterdir() if item.is_file())
    except OSError:
        return []


def _append_review_history(path, session_id, text):
    directory = review_history_dir(path)
    existing = review_history(path)
    sequence = max(
        (
            int(item.name.split("-", 1)[0])
            for item in existing
            if item.name.split("-", 1)[0].isdigit()
        ),
        default=0,
    ) + 1
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    token = _safe(session_id) or "unknown"
    target = directory / ("{:06d}-{}-{}.txt".format(sequence, stamp, token))
    _atomic_write(target, text)
    stale_count = max(0, len(existing) + 1 - review_history_keep())
    for stale in existing[:stale_count]:
        stale.unlink(missing_ok=True)
    return target


# Terminal outcomes that carry NO verdict. Each one is a real event in a repository's review
# record -- the timeout that blocked a commit, the review the operator cancelled, the reviewer
# that failed to start -- and each is recorded in the bounded history WITHOUT touching the
# latest-ruling file, which the next reviewer is shown as its own previous verdict.
OUTCOME_TIMEOUT = "TIMEOUT"
OUTCOME_CANCELLED = "CANCELLED"
OUTCOME_FAILED = "FAILED"
# A review that RAN and returned a ruling which then could not be applied: the staged set moved
# underneath it, or no reviewer thread was captured to attribute it to. Distinct from FAILED,
# because "no verdict was produced" would be false -- one was produced and thrown away, and the
# record keeps it so an operator can see what the reviewer actually said.
OUTCOME_DISCARDED = "DISCARDED"


def record_review_outcome(path, session_id, outcome, detail="", verdict=None):
    """Append one labelled non-verdict outcome to this project's retained history."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    text = str(verdict or "").strip()
    if text:
        explanation = (
            "A verdict was produced but NOT applied; it is preserved below. Any saved latest "
            "ruling for this project is from an earlier review and was deliberately left "
            "untouched."
        )
    else:
        explanation = (
            "No verdict was produced. Any saved latest ruling for this project is from an "
            "earlier review and was deliberately left untouched."
        )
    body = (
        "OUTCOME: {}\n"
        "when: {}\n"
        "session: {}\n"
        "\n"
        "{}\n"
        "\n"
        "detail:\n{}\n"
    ).format(outcome, stamp, str(session_id or "unidentified"), explanation,
             (detail or "").strip() or "(none)")
    if text:
        body += "\nunapplied verdict:\n{}\n".format(text)
    try:
        with _mutation_lock():
            return _append_review_history(path, session_id, body)
    except OSError:
        return None


def clear_review_history(path):
    """Delete this project's retained reviews AND its saved rulings; return the paths removed.

    Both, because both hold the same content -- full verdicts quoting the staged diffs they
    judged, in plaintext on local disk -- and an operator purging review history does not mean
    "leave the newest one behind". Scoped to one canonical project: other repositories'
    reviews are not this command's to delete.
    """
    removed = []
    for item in review_history(path):
        try:
            item.unlink()
        except OSError:
            continue
        removed.append(str(item))
    try:
        review_history_dir(path).rmdir()
    except OSError:
        pass
    stem = _project_stem(path)
    reviews = state_home() / "reviews"
    if stem:
        try:
            candidates = sorted(
                item for item in reviews.iterdir()
                if item.is_file() and item.name.startswith(stem + ".")
                and item.name.endswith(".txt")
            )
        except OSError:
            candidates = []
        for item in candidates:
            try:
                item.unlink()
            except OSError:
                continue
            removed.append(str(item))
    return removed


def save_review(path, session_id, text, history=False):
    target = last_review_file(path, session_id)
    body = str(text or "")
    with _mutation_lock():
        _atomic_write(target, body)
        if history:
            _append_review_history(path, session_id, body)
    return target


def clear_session_artifacts(session_id):
    """Retire ephemeral state when the owning Codex root session ends.

    Session-wide, across every project the session touched: the conversation is over, so none
    of its per-project threads, ledgers, plans or budgets can be resumed. The session token
    leads every filename, so one prefix match finds them all without needing the repositories
    to still exist.
    """
    token = _safe(session_id)
    if not token:
        return
    for directory in ("sessions", "prompts", "plans", "nudges",
                      "stop-permits", "stop-abort-bypasses", "stop-generations",
                      "stop-work-generations"):
        root = state_home() / directory
        for path in (root.glob(token + ".*") if root.is_dir() else ()):
            path.unlink(missing_ok=True)
