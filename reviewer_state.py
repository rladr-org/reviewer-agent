#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""State layer of the reviewer package: settings, per-session identity, and every toggle.

Step 4 of LIB_SPLIT_PLAN.md, and the first extracted layer with a local import: it needs five
names from `reviewer_base` and nothing else. `tools/check_split_layering.py` fails if it ever
reaches forward in the layer order.

This is the largest module and the one everything left depends on, which is why it moves before
manifest, prompts, backends and stopcontinue rather than last (LIB_SPLIT_PLAN.md 5.4).

What belongs here: the settings file and its precedence rules (env > state file > config.json >
default), the model/effort/timeout overrides, the on/off and stopContinue flags, backend
selection, and the per-session ids and paths. What does not: anything that runs a backend,
composes a prompt, or reads a transcript.

`_MIGRATED_CONTROL_STEMS` is shared MUTABLE state; it must be MUTATED and never rebound, since
`reviewer_lib` re-exports it (LIB_SPLIT_PLAN.md 5.6, pinned by tests/test_split_shared_state.py).

Everything here is re-exported by `reviewer_lib` for back-compat, so existing `rl.is_active(...)`
call sites are unchanged.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Claude Code is currently POSIX-only.
    fcntl = None

from reviewer_base import (
    _atomic_write_text,
    _publish_if_absent,
    _sanitize,
    reviewer_home,
    reviewer_state_home,
)

# --------------------------------------------------------------------------
# settings file: one place for every tunable, since hooks run as subprocesses and
# exporting environment variables before launching Claude Code is impractical.
# Precedence throughout is env > state file (slash commands) > config.json > default:
# an env var is a one-off override, an explicit runtime toggle beats a static default,
# and config.json supplies the defaults.
# --------------------------------------------------------------------------
def config_file():
    env = os.environ.get("REVIEWER_CONFIG_FILE")
    return Path(env) if env else reviewer_state_home() / "config.json"


def load_config():
    """The settings dict, or {} if absent/unreadable/malformed. Never raises: a hand-edited
    config with a syntax error must fall back to defaults rather than wedge every hook. Not
    cached -- hooks are short-lived processes, so a read per call is both cheap and always
    current."""
    try:
        data = json.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def config_get(key, default=None):
    val = load_config().get(key)
    return default if val is None else val


def _config_int(key, default):
    """A non-numeric or missing value falls back to the default rather than crashing."""
    try:
        return int(config_get(key, default))
    except (TypeError, ValueError):
        return default


def _tunable_int(env_var, key, default, minimum):
    raw = os.environ.get(env_var)
    if raw is not None:
        try:
            return max(minimum, int(raw))
        except (TypeError, ValueError):
            return default
    return max(minimum, _config_int(key, default))


def codex_bin():
    return (
        os.environ.get("REVIEWER_CODEX_BIN")
        or config_get("codexBin")
        or str(Path.home() / ".local" / "bin" / "codex")
    )


def claude_bin():
    """The `claude` CLI to run as a reviewer child.

    Bare "claude" by default, resolved from PATH: the operator is running Claude Code, so the
    binary is on their PATH by construction, and hardcoding an install location would be wrong
    for every installation method except one."""
    return (
        os.environ.get("REVIEWER_CLAUDE_BIN")
        or config_get("claudeBin")
        or "claude"
    )


# --------------------------------------------------------------------------
# codex model override (global): applied to EVERY reviewer codex call --
# briefs, commit audits, askq answers, stop supervision, including resumes
# (codex accepts a per-turn model override on a resumed thread).
# --------------------------------------------------------------------------
_CODEX_MODEL_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")


def codex_model_file():
    return reviewer_home() / "state" / "codex_model"


def codex_model_detail():
    """(model, source). Resolution: env REVIEWER_CODEX_MODEL (one-off/test override) > the
    persistent state file (set via /reviewerModel) > config.json `model` > (None, ...) ->
    inherit the codex CLI's own configured default.

    config.json supplies the DEFAULT model; /reviewerModel (the state file) is the explicit
    runtime choice and wins. The toggle validates on SET, but the env var, a hand-edited state
    file and config.json all bypass it -- so every candidate is re-validated here and an INVALID
    one is deterministically SKIPPED, ensuring a malformed token never reaches `-c model="..."`.

    Returns the source so display code never restates this precedence. It did, and drifted:
    `/reviewerModel` called any non-empty env var the source, so with an invalid
    REVIEWER_CODEX_MODEL it reported `from env` for a model the state file had supplied -- and an
    operator would then unset a variable that was already being ignored."""
    for source, candidate in (
        ("env REVIEWER_CODEX_MODEL", os.environ.get("REVIEWER_CODEX_MODEL")),
        (str(codex_model_file()), _read_state_text(codex_model_file())),
        ("config.json model", config_get("model")),
    ):
        val = str(candidate or "").strip()
        if val and is_valid_codex_model(val):
            return val, source
    return None, "codex CLI default"


def codex_model():
    """The model every reviewer call uses, or None to inherit the codex CLI default. Thin
    wrapper so there is exactly ONE place the precedence is implemented."""
    return codex_model_detail()[0]


def is_valid_codex_model(name):
    """Single plain token only, so a typo with spaces/quotes can never produce a
    malformed `-c model="..."` TOML override."""
    return bool(_CODEX_MODEL_RE.match(name or ""))


def set_codex_model(model):
    f = codex_model_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(model.strip() + "\n", encoding="utf-8")


def clear_codex_model():
    codex_model_file().unlink(missing_ok=True)


def codex_model_config(model):
    return 'model="{}"'.format(model)


# --------------------------------------------------------------------------
# codex reasoning-effort override (global): applied to the RESUME path only --
# the commit audit and the askq answer, which are the calls whose quality is
# being bought. Two paths deliberately keep their own effort:
#
#   - the BRIEF is hardcoded to BRIEF_REASONING_CONFIG. Its response text is
#     discarded (it exists to mint a session id and seed it), so raising it
#     buys latency and nothing else.
#   - the STOP supervisor passes stopcontinue_effort_config() via extra_config
#     at every turn end. It survives because codex applies repeated `-c`
#     overrides LAST-WINS and resume_by_id_argv appends extra_config after
#     this one -- measured on codex-cli 0.146.0, both orders, not assumed.
#
# The accepted set is CLOSED and known, so this validates against a whitelist
# rather than a token regex. That difference is load-bearing: an unrecognised
# level is not tolerated the way an unrecognised service_tier is, but returns
# HTTP 400 `[reasoning.effort] [invalid_enum_value]` and FAILS the call. The
# commit gate is fail-CLOSED, so a well-formed typo in the state file would
# block every commit until someone found it.
#
# The whitelist is the union of two sources that each under-report the other:
# that 400's own message enumerates `none, minimal, low, medium, high, xhigh,
# max`, while ~/.codex/models_cache.json lists `ultra` for gpt-5.6-sol and
# gpt-5.6-terra -- and `ultra` was accepted end-to-end on gpt-5.6-luna despite
# being absent from the enum. Levels are per-model (gpt-5.5 and gpt-5.4 stop at
# xhigh), but no per-model check is made: the catalog demonstrably does not
# bound what runs, so refusing a level on its authority would be a claim the
# evidence does not support.
# --------------------------------------------------------------------------
CODEX_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")


def codex_effort_file():
    return reviewer_home() / "state" / "codex_effort"


def codex_effort_detail():
    """(effort, source). Resolution mirrors `codex_model_detail` exactly: env
    REVIEWER_CODEX_EFFORT (one-off/test) > the state file (set via /reviewerModel) >
    config.json `reasoningEffort` > (None, ...) -> inherit the codex CLI's own configured
    effort, which is what every reviewer call did before this override existed.

    Every candidate is re-validated and an invalid one is deterministically SKIPPED so it
    can never reach `-c model_reasoning_effort="..."`. The command validates on set, but the
    env var, a hand-edited state file and config.json all bypass it."""
    for source, candidate in (
        ("env REVIEWER_CODEX_EFFORT", os.environ.get("REVIEWER_CODEX_EFFORT")),
        (str(codex_effort_file()), _read_state_text(codex_effort_file())),
        ("config.json reasoningEffort", config_get("reasoningEffort")),
    ):
        val = str(candidate or "").strip()
        if val and is_valid_codex_effort(val):
            return val, source
    return None, "codex CLI default"


def codex_effort():
    """The reasoning effort the commit audit and askq answer use, or None to inherit the
    codex CLI default. One place implements the precedence."""
    return codex_effort_detail()[0]


def is_valid_codex_effort(level):
    """Whitelist membership, exact and case-sensitive. See CODEX_EFFORTS above for why this
    is a closed set rather than a token pattern."""
    return isinstance(level, str) and level.strip() in CODEX_EFFORTS


def set_codex_effort(level):
    f = codex_effort_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(level.strip() + "\n", encoding="utf-8")


def clear_codex_effort():
    codex_effort_file().unlink(missing_ok=True)


def codex_effort_config(level):
    return 'model_reasoning_effort="{}"'.format(level)


# --------------------------------------------------------------------------
# Commit-audit timeout. Set alongside the model (`/reviewerModel <model> <secs>` or
# `/reviewerModel timeout <secs>`) because it is the same kind of knob: a global runtime
# property of every reviewer call, adjusted per machine, not per project.
#
# Evidenced: a ~5700-line staged diff exceeded the previous hardcoded 600s. The commit gate
# is fail-CLOSED, so a timeout BLOCKS the commit (correct -- a timeout carries no verdict, and
# letting an unreviewed commit through would defeat the gate). The remedy is therefore a
# bigger budget, not a softer failure.
#
# Scoped to the COMMIT audit deliberately. The stop supervisor (300s) and the question
# answerer (300s) are latency-sensitive -- they run at every turn end -- and neither is
# fail-closed, so a slow one costs a wasted turn, never a blocked commit.
# --------------------------------------------------------------------------
DEFAULT_COMMIT_TIMEOUT = 900
DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT = 3


COMMIT_TIMEOUT_FLOOR = 60


def commit_timeout_file():
    return reviewer_home() / "state" / "commit_timeout"


def _positive_int(value):
    """`value` as a positive int, or None. Rejects floats-as-strings and negatives so a
    malformed override deterministically falls through to the next source."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def commit_review_timeout_detail():
    """(seconds, source) for the commit audit. Resolution mirrors `codex_model`: env
    REVIEWER_COMMIT_TIMEOUT_SECS (one-off/test) > the state file (/reviewerModel) >
    config.json `commitReviewTimeoutSecs` > DEFAULT_COMMIT_TIMEOUT.

    Returns the SOURCE alongside the value so no caller has to restate the precedence rules to
    display them. `/reviewerModel` did restate them, and drifted: it called any non-empty env
    var authoritative, while resolution SKIPS an invalid one -- so it could report `from env`
    for a value that came from the state file, sending an operator to edit the wrong place.

    Every candidate is clamped to >= COMMIT_TIMEOUT_FLOOR; an unusable one (0, negative,
    non-integer) is skipped entirely so it cannot mask a real setting further down the chain.
    The toggle already rejects a too-small value, but the env var and a hand-edited state file
    bypass that -- and here a bad value is not cosmetic: a 1-second timeout would make the
    fail-closed gate block EVERY commit."""
    for source, candidate in (
        ("env REVIEWER_COMMIT_TIMEOUT_SECS", os.environ.get("REVIEWER_COMMIT_TIMEOUT_SECS")),
        (str(commit_timeout_file()), _read_state_text(commit_timeout_file())),
        ("config.json commitReviewTimeoutSecs", config_get("commitReviewTimeoutSecs")),
    ):
        n = _positive_int(candidate) if candidate not in (None, "") else None
        if n is not None:
            return max(COMMIT_TIMEOUT_FLOOR, n), source
    return DEFAULT_COMMIT_TIMEOUT, "default"


def commit_review_timeout():
    """Seconds the commit audit may take. Thin wrapper so there is exactly ONE place the
    precedence is implemented."""
    return commit_review_timeout_detail()[0]


def _read_state_text(path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def set_commit_timeout(seconds):
    f = commit_timeout_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(int(seconds)) + "\n", encoding="utf-8")


def clear_commit_timeout():
    commit_timeout_file().unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Codex-streaming hang detection uses TWO idle thresholds, each resolved exactly like
# commit_review_timeout (env > state file > config.json > default, each candidate clamped to a
# floor and an unusable one skipped). N1 (first-output) catches a reviewer that never produced
# any output; N2 (between-output) catches one that fell silent mid-run. Both floors matter for
# the same reason the commit-timeout floor does: the gate is fail-CLOSED, so a 1-second threshold
# would kill every audit before it could finish. See docs/plans/codex_jsonl_streaming.md.
# --------------------------------------------------------------------------
DEFAULT_FIRST_OUTPUT_TIMEOUT = 30
FIRST_OUTPUT_TIMEOUT_FLOOR = 5
DEFAULT_IDLE_TIMEOUT = 900
IDLE_TIMEOUT_FLOOR = 60


def _timeout_detail(env_var, state_file, config_key, default, floor):
    """(seconds, source) for a timeout knob, mirroring commit_review_timeout_detail's precedence
    and clamping verbatim -- factored out because the two streaming thresholds share it exactly."""
    for source, candidate in (
        ("env " + env_var, os.environ.get(env_var)),
        (str(state_file), _read_state_text(state_file)),
        ("config.json " + config_key, config_get(config_key)),
    ):
        n = _positive_int(candidate) if candidate not in (None, "") else None
        if n is not None:
            return max(floor, n), source
    return default, "default"


def first_output_timeout_file():
    return reviewer_home() / "state" / "first_output_timeout"


def first_output_timeout_detail():
    """(seconds, source) for N1, the first-output timeout: no byte on stdout/stderr within this
    many seconds of launch means the reviewer never started, so it is killed and retried once."""
    return _timeout_detail(
        "REVIEWER_FIRST_OUTPUT_TIMEOUT_SECS", first_output_timeout_file(),
        "firstOutputTimeoutSecs", DEFAULT_FIRST_OUTPUT_TIMEOUT, FIRST_OUTPUT_TIMEOUT_FLOOR)


def first_output_timeout():
    return first_output_timeout_detail()[0]


def set_first_output_timeout(seconds):
    f = first_output_timeout_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(int(seconds)) + "\n", encoding="utf-8")


def clear_first_output_timeout():
    first_output_timeout_file().unlink(missing_ok=True)


def idle_timeout_file():
    return reviewer_home() / "state" / "idle_timeout"


def idle_timeout_detail():
    """(seconds, source) for N2, the between-output timeout: after the first byte, a gap longer
    than this means the reviewer fell silent mid-run. Must exceed the longest legitimate silent
    item (a full test run), which is why its default and floor are much larger than N1's."""
    return _timeout_detail(
        "REVIEWER_IDLE_TIMEOUT_SECS", idle_timeout_file(),
        "idleTimeoutSecs", DEFAULT_IDLE_TIMEOUT, IDLE_TIMEOUT_FLOOR)


def idle_timeout():
    return idle_timeout_detail()[0]


def set_idle_timeout(seconds):
    f = idle_timeout_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(int(seconds)) + "\n", encoding="utf-8")


def clear_idle_timeout():
    idle_timeout_file().unlink(missing_ok=True)


def reviewer_session_audit_limit_file():
    return reviewer_home() / "state" / "reviewer_session_audit_limit"


def _audit_limit(value):
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
        ("env REVIEWER_SESSION_AUDIT_LIMIT", os.environ.get("REVIEWER_SESSION_AUDIT_LIMIT")),
        (str(reviewer_session_audit_limit_file()),
         _read_state_text(reviewer_session_audit_limit_file())),
        ("config.json reviewerSessionAuditLimit", config_get("reviewerSessionAuditLimit")),
        ("default", DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT),
    ):
        parsed = _audit_limit(candidate)
        if parsed is not None:
            return parsed, source
    return DEFAULT_REVIEWER_SESSION_AUDIT_LIMIT, "default"


def reviewer_session_audit_limit():
    return reviewer_session_audit_limit_detail()[0]


def set_reviewer_session_audit_limit(value):
    parsed = _audit_limit(value)
    if parsed is None:
        raise ValueError("invalid reviewer session audit limit")
    _atomic_write_text(reviewer_session_audit_limit_file(), str(parsed) + "\n")


def clear_reviewer_session_audit_limit():
    reviewer_session_audit_limit_file().unlink(missing_ok=True)


def commit_retry_file(proj, claude_session_id):
    stem = _project_session_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".commit-retry.pending")


def commit_retry_pending(proj, claude_session_id):
    f = commit_retry_file(proj, claude_session_id)
    return bool(f and f.exists())


def set_commit_retry_pending(proj, claude_session_id):
    f = commit_retry_file(proj, claude_session_id)
    if f is not None:
        _atomic_write_text(f, "pending\n")


def clear_commit_retry_pending(proj, claude_session_id):
    f = commit_retry_file(proj, claude_session_id)
    if f is not None:
        f.unlink(missing_ok=True)


def _carryover_marker_stem(proj, claude_session_id):
    """SHORT, bounded, session-scoped stem for the carryover markers. Like `_watchdog_marker_stem`:
    the readable `_project_session_stem` grows with the project path and the atomic writer adds a
    temp-UUID sibling on top, so for long (but valid) project paths the sum overflows the 255-byte
    filename limit -- the atomic write then fails ENAMETOOLONG and the one-shot marker silently
    never appears (even best-effort), so automatic rotation loses the preserved findings. A
    fixed-length digest of (canonical project, session) keeps the marker AND its temp sibling
    comfortably bounded while preserving per-(project, session) isolation."""
    sid = str(claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return None
    digest = hashlib.sha1(
        (canonical_project_key(proj) + "\x00" + sid).encode("utf-8")
    ).hexdigest()
    return "cc_" + digest[:20]


def commit_carryover_file(proj, claude_session_id):
    """The ARMED one-shot marker: the reviewer session for this (project, Claude session) was
    RETIRED with outstanding findings preserved, so the SUCCESSOR's first review must be handed
    those findings to verify. Keyed per (project, Claude session) so two sessions in one repo never
    cross-carry."""
    stem = _carryover_marker_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".pending")


def commit_carryover_pending(proj, claude_session_id):
    """Non-consuming existence check (tests, observability, and the commit hook's 're-arm on a
    failed review' decision). Best-effort: a state-path error reads as not-armed rather than
    raising."""
    f = commit_carryover_file(proj, claude_session_id)
    if f is None:
        return False
    try:
        return f.exists()
    except OSError:
        return False


def set_commit_carryover(proj, claude_session_id):
    """ARM the one-shot marker with a FRESH generation token (rotation, on a preserved rejection).
    The token lets consumption target exactly the generation a review read and delivered, so an
    overlapping rotation that re-arms is not unlinked undelivered. Best-effort: a state-path error
    (e.g. ENAMETOOLONG) must not crash the retiring hook."""
    f = commit_carryover_file(proj, claude_session_id)
    if f is not None:
        try:
            _atomic_write_text(f, uuid.uuid4().hex + "\n")
        except OSError:
            pass


def commit_carryover_generation(proj, claude_session_id):
    """The armed marker's generation token (its content), or None when not armed / unreadable. A
    fresh token is written on each arm, so a review can record WHICH generation it read and delivered
    and consumption can target exactly that one."""
    f = commit_carryover_file(proj, claude_session_id)
    if f is None:
        return None
    try:
        token = f.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return token or None


def consume_commit_carryover(proj, claude_session_id, generation):
    """Delete the marker ONLY if it still holds `generation` (the token this review read and
    delivered), under the rotation audit lock so an overlapping re-arm of a NEWER generation is left
    for its own successor rather than unlinked undelivered. Returns whether it deleted."""
    f = commit_carryover_file(proj, claude_session_id)
    if f is None or not generation:
        return False
    # FULLY best-effort: this must never raise, because commit_hook calls it before emitting its
    # response. The lock acquisition ITSELF does mkdir/open (see _reviewer_session_audit_lock), so
    # the `with` is placed INSIDE this `try` -- a lock-acquisition OSError is caught here too, not
    # only read/unlink errors.
    try:
        with _reviewer_session_audit_lock():  # acquisition (mkdir/open) guarded by the try above
            current = f.read_text(encoding="utf-8").strip()
            if current != generation:
                return False  # a newer generation was armed -> do not delete it
            f.unlink()
            return True
    except OSError:
        return False


def clear_commit_carryover(proj, claude_session_id):
    """Best-effort discard of the (non-consuming) carryover marker -- used on a clean retirement and
    other cleanup paths. Inheritance is gated by the session-mismatch state, not by deleting this,
    so clearing it only tidies state; it never affects a fresh successor's inheritance."""
    f = commit_carryover_file(proj, claude_session_id)
    if f is not None:
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass


def project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()


# Budget for the readable half of a project stem, in BYTES. A filename component is capped at 255
# bytes on the filesystems this runs on, and the longest suffix appended to a stem is
# ".sess_<uuid-session-id>.reviewer.codex" (57). 160 + 1 + 20 (digest) + 57 = 238.
_MAX_STEM_BYTES = 160


_STEM_DIGEST_LEN = 20


def _state_stem(key):
    """A filesystem-safe, collision-free, byte-bounded stem for a project key.

    Two distinct failures, one helper:

    - `_sanitize()` maps BOTH "/" and " " to "_", so "/tmp/a b/repo" and "/tmp/a_b/repo" collapse
      to the same name. Sharing a stem means two DIFFERENT repositories sharing a saved verdict,
      a review history and an audited-stage record. The digest is therefore unconditional, not a
      fallback for over-long keys: short paths are the common case and collide just as readily.
    - a filename component is capped in BYTES, not characters, so the readable half is trimmed on
      its encoded length. Bounding on len() let a multibyte path overflow anyway, and every
      writer keyed this way swallows OSError -- so the file silently never appeared.

    The readable half is for humans reading the state directory; uniqueness comes entirely from
    the digest, so trimming it is always safe."""
    digest = hashlib.sha256(str(key).encode("utf-8", "surrogateescape")).hexdigest()[
        :_STEM_DIGEST_LEN]
    # Slicing bytes can cut a multibyte character in half; "ignore" drops the partial tail.
    readable = _sanitize(key).encode("utf-8", "surrogateescape")[:_MAX_STEM_BYTES]
    return readable.decode("utf-8", "ignore") + "_" + digest


def canonical_project_key(path):
    """Stable per-PROJECT key shared by the main checkout, every linked worktree, and
    any subdirectory. Git's common dir is shared across a repo's worktrees, so
    dirname(realpath(--git-common-dir)) resolves to the main worktree root from anywhere
    inside the project. Falls back to realpath(path) when `path` is not inside a git repo
    (or git is unavailable), preserving the previous raw-path behavior for non-repos."""
    raw = os.path.realpath(str(path))
    try:
        p = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return raw
    if p.returncode != 0:
        return raw
    common = p.stdout.strip()
    if not common:
        return raw
    if not os.path.isabs(common):
        common = os.path.join(str(path), common)
    return os.path.dirname(os.path.realpath(common))


# The per-project files whose EXISTENCE (or contents) is an operator setting, as opposed to a
# cache the harness can rebuild. Renaming these silently changes behaviour, so they migrate.
_PROJECT_CONTROL_SUFFIXES = (".active", ".backend", ".stopcontinue.active", ".directives.json")


# Stems this PROCESS has already considered. Hook processes are short-lived, so this simply
# stops is_active() -- called on every single Bash tool call -- from re-statting the legacy
# names for the rest of the run.
_MIGRATED_CONTROL_STEMS = set()


def _migrate_project_control_state(key, stem):
    """Adopt settings written under the pre-digest filename. At most once per project.

    The old stem was `_sanitize()` alone, which maps both "/" and " " to "_", so distinct
    repositories shared activation, backend, stopContinue and directive state. Three decisions
    make the rename safe:

    COPY, not move. Two projects can be sharing one legacy file right now, and both are
    observably behaving as if it were theirs; moving it would turn one of them off with no
    message.

    A sentinel, so a completed migration is never repeated. Otherwise `/reviewerAgent off`
    deletes the new file and the very next call resurrects it from the legacy copy -- the toggle
    would appear broken.

    Nothing is written unless something was actually copied. A path accessor that writes on
    every call is a surprise in itself: it would drop a sentinel into the state directory for
    every project ever inspected, including throwaway ones.

    Never raises: these accessors are called from a fail-closed gate, and a migration failure
    must not take a commit down with it. The worst case is the pre-existing behaviour."""
    if stem in _MIGRATED_CONTROL_STEMS:
        return
    _MIGRATED_CONTROL_STEMS.add(stem)
    state = reviewer_home() / "state"
    legacy = _sanitize(key)
    if legacy == stem or (state / (stem + ".migrated")).exists():
        return
    try:
        copied = False
        for suffix in _PROJECT_CONTROL_SUFFIXES:
            src, dst = state / (legacy + suffix), state / (stem + suffix)
            if src.exists() and not dst.exists():
                state.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(src), str(dst))
                copied = True
        if copied:
            (state / (stem + ".migrated")).write_text("", encoding="utf-8")
    except OSError:
        return


def _project_control_stem(proj):
    """The collision-free stem for `proj`'s control files, migrating legacy state on first use.

    Resolves the canonical key ONCE: it shells out to git, and these accessors are called
    several times per hook invocation."""
    key = canonical_project_key(proj)
    stem = _state_stem(key)
    _migrate_project_control_state(key, stem)
    return stem


def flag_file(proj):
    # Keyed by the CANONICAL project root so the toggle set from the main checkout, a
    # linked worktree, or a subdirectory all resolve to the same flag (the worktree fix).
    return reviewer_home() / "state" / (_project_control_stem(proj) + ".active")


# --------------------------------------------------------------------------
# explicit risk acceptance: one receipt per host/repository, shared by both
# reviewerAgent and stopContinue and invalidated whenever DISCLAIMER.md changes.
# --------------------------------------------------------------------------
def disclaimer_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def risk_acceptance_file(proj):
    return reviewer_home() / "state" / (
        _project_control_stem(proj) + ".risk.accepted.json"
    )


def risk_accepted(proj, disclaimer_hash):
    try:
        record = json.loads(risk_acceptance_file(proj).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(
        isinstance(record, dict)
        and record.get("project") == canonical_project_key(proj)
        and record.get("disclaimer_sha256") == str(disclaimer_hash)
    )


def record_risk_acceptance(proj, disclaimer_hash, source):
    root = canonical_project_key(proj)
    target = risk_acceptance_file(root)
    _atomic_write_text(
        target,
        json.dumps(
            {
                "accepted_at_unix": int(time.time()),
                "disclaimer_sha256": str(disclaimer_hash),
                "project": root,
                "source": str(source),
            },
            sort_keys=True,
        )
        + "\n",
    )
    return target


# --------------------------------------------------------------------------
# per-project on/off flag
# --------------------------------------------------------------------------
def is_active(proj):
    return flag_file(proj).exists()


def set_active(proj):
    f = flag_file(proj)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("on\n")


def clear_active(proj):
    flag_file(proj).unlink(missing_ok=True)


def any_reviewer_active():
    """True if the commit gate is active for ANY project (some `<stem>.active` flag exists,
    excluding the `.stopcontinue[.watchdog].active` flags, which are a different feature). Used to
    fail an unresolvable `git -C <target>` commit CLOSED regardless of the session cwd's own
    activation: the gate cannot expand the target, so it could be committing to one of those active
    repos unreviewed. When nothing is active the plugin stays inert (off means off) and such a
    commit is left alone. Best-effort: a state-path error reads as 'nothing active'."""
    state = reviewer_home() / "state"
    try:
        for f in state.glob("*.active"):
            name = f.name
            if name.endswith(".stopcontinue.active") or \
                    name.endswith(".stopcontinue_watchdog.active"):
                continue
            return True
    except OSError:
        return False
    return False


# --------------------------------------------------------------------------
# reviewer backend selection (codex | pi). Backend files store ONLY the token;
# the model lives in separate per-backend slots (see reviewer_model below).
# Resolution: env REVIEWER_BACKEND > per-project override > global default > codex.
# --------------------------------------------------------------------------
_BACKENDS = ("codex", "pi", "claude")


def is_valid_backend(name):
    return name in _BACKENDS


def backend_default_file():
    return reviewer_home() / "state" / "backend.default"


def project_backend_file(proj):
    return reviewer_home() / "state" / (_project_control_stem(proj) + ".backend")


def _read_backend_token(path):
    """A valid backend token from a one-line file, or None. Missing / unreadable /
    invalid content is skipped so a corrupt file can never select a bogus backend."""
    try:
        val = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return val if is_valid_backend(val) else None


def reviewer_backend(proj):
    """Resolve the reviewer backend for `proj`: env REVIEWER_BACKEND (one-off/test)
    > per-project override file > global default file > "codex". Every candidate is
    validated; an invalid one is skipped so a typo never selects a missing backend."""
    env = (os.environ.get("REVIEWER_BACKEND") or "").strip()
    if is_valid_backend(env):
        return env
    cfg = (str(config_get("backend") or "").strip())
    return (_read_backend_token(project_backend_file(proj))
            or _read_backend_token(backend_default_file())
            or (cfg if is_valid_backend(cfg) else None)
            or "codex")


def _write_backend(path, backend):
    if not is_valid_backend(backend):
        raise ValueError("invalid backend {!r}; expected one of {}".format(backend, _BACKENDS))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(backend + "\n", encoding="utf-8")


def set_project_backend(proj, backend):
    _write_backend(project_backend_file(proj), backend)


def clear_project_backend(proj):
    project_backend_file(proj).unlink(missing_ok=True)


def set_default_backend(backend):
    _write_backend(backend_default_file(), backend)


# --------------------------------------------------------------------------
# per-backend model slots: reviewer_model(backend). codex reuses the existing
# codex_model() slot (state/codex_model); pi uses state/pi_model. Env override
# REVIEWER_<BACKEND>_MODEL wins. Model has NO project scope (global slots).
# --------------------------------------------------------------------------
def pi_model_file():
    return reviewer_home() / "state" / "pi_model"


def claude_model_file():
    return reviewer_home() / "state" / "claude_model"


def _backend_model_file(backend):
    if backend == "codex":
        return codex_model_file()
    if backend == "pi":
        return pi_model_file()
    if backend == "claude":
        return claude_model_file()
    raise ValueError("invalid backend {!r}; expected one of {}".format(backend, _BACKENDS))


def reviewer_model_detail(backend):
    """(model, source) for `backend`, so display code never restates the precedence.

    The chains are NOT the same length and must not be flattened into one loop:

    - codex:  env REVIEWER_CODEX_MODEL > state/codex_model > config.json `model`. Delegated to
      codex_model_detail() rather than reimplemented -- a hand-rolled copy silently drops the
      config.json candidate, which is the whole reason that function returns its source.
    - pi:     env REVIEWER_PI_MODEL > state/pi_model. There is deliberately no config.json key.
    - claude: env REVIEWER_CLAUDE_MODEL > state/claude_model, on the same terms as pi.

    An unknown backend RAISES, matching _backend_model_file: a typo that fell back to the codex
    slot would report a model the reviewer in use never reads, which is the bug this whole
    function exists to remove.
    """
    if backend == "codex":
        return codex_model_detail()
    f = _backend_model_file(backend)   # raises on an unknown backend
    env_var = "REVIEWER_" + backend.upper() + "_MODEL"
    for source, candidate in (
        ("env " + env_var, os.environ.get(env_var)),
        (str(f), _read_state_text(f)),
    ):
        val = str(candidate or "").strip()
        # An INVALID candidate is skipped, not reported: naming it as the source would send an
        # operator to unset a variable that was already being ignored.
        if val and is_valid_codex_model(val):
            return val, source
    return None, "unset"


def reviewer_model(backend):
    """Model for `backend`, or None to inherit the tool's own default. Thin wrapper so there is
    exactly ONE place each backend's precedence is implemented."""
    return reviewer_model_detail(backend)[0]


# --------------------------------------------------------------------------
# per-backend reasoning-effort slots. codex reuses state/codex_effort; pi uses state/pi_effort;
# claude uses state/claude_effort.
#
# The LEVEL SETS are deliberately separate and must never be merged. `pi --help` documents
# off|minimal|low|medium|high|xhigh|max, codex takes none..ultra, and claude takes low..max: pi
# has `off` where codex has `none`, claude has neither and also no `minimal` or `ultra`.
# Validating one backend's level against another's list would let an unknown level reach the CLI,
# which REJECTS it -- and since the commit gate is fail-closed, a rejected review blocks every
# commit in the project until someone traces it back.
# --------------------------------------------------------------------------
PI_EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")

# What `claude --effort` accepts, and nothing else. Codex's `none` and `ultra` are absent, and
# pi's `off` is too: sending a level the CLI does not know fails the call, and under a
# fail-closed gate a rejected call blocks every commit in the project.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")

# What the reviewer sends pi when nothing is configured. Not "no setting": the flag is always
# passed, so an unset override is this level, and the resolver reports it as such rather than
# describing a level that IS being sent as absent.
PI_DEFAULT_EFFORT = "medium"

# The flag is always sent, so an unset override IS this level rather than "no setting". Medium
# matches pi's default and the CLI's own middle setting; a reviewer wanting more depth sets it
# explicitly with `/reviewerModel effort <level>`.
CLAUDE_DEFAULT_EFFORT = "medium"


def _backend_default_effort(backend):
    return CLAUDE_DEFAULT_EFFORT if backend == "claude" else PI_DEFAULT_EFFORT


def pi_effort_file():
    return reviewer_home() / "state" / "pi_effort"


def claude_effort_file():
    return reviewer_home() / "state" / "claude_effort"


def reviewer_efforts(backend):
    """The valid level set for `backend`. Raises on an unknown backend."""
    if backend == "codex":
        return CODEX_EFFORTS
    if backend == "pi":
        return PI_EFFORTS
    if backend == "claude":
        return CLAUDE_EFFORTS
    raise ValueError("invalid backend {!r}; expected one of {}".format(backend, _BACKENDS))


def _backend_effort_file(backend):
    if backend == "codex":
        return codex_effort_file()
    if backend == "pi":
        return pi_effort_file()
    if backend == "claude":
        return claude_effort_file()
    raise ValueError("invalid backend {!r}; expected one of {}".format(backend, _BACKENDS))


def is_valid_backend_effort(backend, level):
    return isinstance(level, str) and level.strip() in reviewer_efforts(backend)


def reviewer_effort_detail(backend):
    """(effort, source) for `backend`, so display code never restates the precedence.

    codex delegates to codex_effort_detail() -- which also reads config.json `reasoningEffort`,
    a candidate a hand-rolled copy would drop -- and returns None to mean "inherit the CLI's own
    configured effort". pi and claude have no config.json key and no inherit case: the flag is
    always sent, so an unset override resolves to that backend's default with source "default".

    Every candidate is re-validated and an invalid one is deterministically SKIPPED. The toggle
    validates what it writes, but the env var and a hand-edited state file bypass it entirely,
    and an invalid level reaching the CLI fails the review under a fail-closed gate.
    """
    if backend == "codex":
        return codex_effort_detail()
    f = _backend_effort_file(backend)   # raises on an unknown backend
    env_var = "REVIEWER_" + backend.upper() + "_EFFORT"
    for source, candidate in (
        ("env " + env_var, os.environ.get(env_var)),
        (str(f), _read_state_text(f)),
    ):
        val = str(candidate or "").strip()
        if val and is_valid_backend_effort(backend, val):
            return val, source
    return _backend_default_effort(backend), "default"


def reviewer_effort(backend):
    """The reasoning level `backend` will run at. One place implements the precedence."""
    return reviewer_effort_detail(backend)[0]


def set_backend_effort(backend, level):
    f = _backend_effort_file(backend)   # raises on an unknown backend
    if not is_valid_backend_effort(backend, level):
        raise ValueError(
            "invalid reasoning effort {!r} for backend {!r}; expected one of: {}".format(
                level, backend, ", ".join(reviewer_efforts(backend)))
        )
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(level.strip() + "\n", encoding="utf-8")


def clear_backend_effort(backend):
    _backend_effort_file(backend).unlink(missing_ok=True)


def set_backend_model(backend, model):
    if not is_valid_codex_model(model):
        raise ValueError("invalid model {!r} (single token [A-Za-z0-9._:/-] only)".format(model))
    f = _backend_model_file(backend)   # raises on invalid backend
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(model.strip() + "\n", encoding="utf-8")


def clear_backend_model(backend):
    _backend_model_file(backend).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# stopContinue: a SEPARATE per-project flag for the Stop hook, which forwards
# prose questions (the agent paused without AskUserQuestion) to the reviewer.
# Independent of the main reviewer flag so it is opt-in on its own.
# --------------------------------------------------------------------------
def stopcontinue_flag_file(proj):
    return reviewer_home() / "state" / (_project_control_stem(proj) + ".stopcontinue.active")


def is_stopcontinue_active(proj):
    return stopcontinue_flag_file(proj).exists()


def set_stopcontinue_active(proj):
    f = stopcontinue_flag_file(proj)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("on\n")


def clear_stopcontinue_active(proj):
    stopcontinue_flag_file(proj).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# stopContinueWatchdog: a per-PROJECT flag + interval for the CronCreate-armed
# idle-strand recovery loop. The cron re-invokes an idle session so the EXISTING
# Stop hook (stopContinue) can recover an unsupervised fail-open strand -- so this
# REQUIRES stopContinue to be active (the tick is inert otherwise). The flag file's
# CONTENT is the interval in minutes. See docs/plans/stopcontinue_watchdog.md.
# --------------------------------------------------------------------------
WATCHDOG_DEFAULT_INTERVAL = 7
WATCHDOG_MIN_INTERVAL = 1
WATCHDOG_MAX_INTERVAL = 59  # `*/N` in the cron minute field is not a general >1h interval


def stopcontinue_watchdog_flag_file(proj):
    return reviewer_home() / "state" / (
        _project_control_stem(proj) + ".stopcontinue_watchdog.active")


def is_stopcontinue_watchdog_active(proj):
    return stopcontinue_watchdog_flag_file(proj).exists()


def is_valid_watchdog_interval(n):
    return isinstance(n, int) and WATCHDOG_MIN_INTERVAL <= n <= WATCHDOG_MAX_INTERVAL


def set_stopcontinue_watchdog_active(proj, interval=WATCHDOG_DEFAULT_INTERVAL):
    if not is_valid_watchdog_interval(interval):
        raise ValueError("watchdog interval must be an integer 1-59 minutes")
    f = stopcontinue_watchdog_flag_file(proj)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(str(interval) + "\n")


def clear_stopcontinue_watchdog_active(proj):
    stopcontinue_watchdog_flag_file(proj).unlink(missing_ok=True)


def watchdog_interval(proj):
    """The armed interval in minutes. A missing or corrupt flag file -> the default, so a
    hand-edit can never produce an out-of-range `*/N` cron expression."""
    raw = _read_state_text(stopcontinue_watchdog_flag_file(proj))
    try:
        n = int((raw or "").strip())
    except (ValueError, TypeError):
        return WATCHDOG_DEFAULT_INTERVAL
    return n if is_valid_watchdog_interval(n) else WATCHDOG_DEFAULT_INTERVAL


def is_stopcontinue_watchdog_effective(proj):
    """The watchdog only DOES anything while stopContinue is also active: its tick relies on the
    Stop hook, which returns immediately when the stopContinue flag is off. `on` enforces this up
    front, but stopContinue can be turned off LATER -- and clearing the stopContinue flag does not
    touch the watchdog flag. So the tick's self-cancel check and the SessionStart re-arm both gate
    on this combined predicate: with stopContinue off the watchdog reports INACTIVE, so its crons
    self-cancel and re-arming stops, WITHOUT the stopContinue toggle needing to know it exists."""
    return is_stopcontinue_active(proj) and is_stopcontinue_watchdog_active(proj)


# --------------------------------------------------------------------------
# Watchdog auto-shutoff markers (per Claude session, not per project).
# See docs/plans/watchdog_auto_shutoff.md. All timing is decided HERE in Python (the tick is a
# pure presence check): the Stop hook writes `disarm` (a timestamp) on a genuine STOP_OK and,
# on later idle ticks, writes `teardown_ready` once the disarm marker is at least one interval
# old. Session-scoped so two Claude sessions in one repo never tear down each other's cron.
# --------------------------------------------------------------------------
def _watchdog_marker_stem(proj, claude_session_id):
    """A SHORT, bounded, session-scoped stem for the watchdog markers. The readable
    `_project_session_stem` grows with the project path, and the atomic writer adds a temp-UUID
    sibling on top -- for long (but valid) project paths that sum can exceed the filesystem's name
    limit, at which point the Stop hook's fail-open swallows ENAMETOOLONG and auto-shutoff silently
    stops. A fixed-length digest of (canonical project, session) keeps the marker AND its temp
    sibling comfortably bounded while preserving session isolation. The session id is still stored
    IN the disarm marker's JSON body for debuggability."""
    sid = str(claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return None
    digest = hashlib.sha1(
        (canonical_project_key(proj) + "\x00" + sid).encode("utf-8")
    ).hexdigest()
    return "wd_" + digest[:20]


def watchdog_disarm_file(proj, claude_session_id):
    stem = _watchdog_marker_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".disarm")


def watchdog_teardown_ready_file(proj, claude_session_id):
    stem = _watchdog_marker_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".teardown_ready")


def is_watchdog_disarm_set(proj, claude_session_id):
    f = watchdog_disarm_file(proj, claude_session_id)
    return bool(f and f.exists())


def set_watchdog_disarm(proj, claude_session_id):
    """Record a completion at `now`. Presence + the `ts` are load-bearing; the ts drives the
    in-hook age check (the tick never reads it)."""
    f = watchdog_disarm_file(proj, claude_session_id)
    if f is None:
        return
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _atomic_write_text(f, json.dumps({"ts": ts, "session_id": str(claude_session_id)}) + "\n")


def clear_watchdog_disarm(proj, claude_session_id):
    f = watchdog_disarm_file(proj, claude_session_id)
    if f is not None:
        f.unlink(missing_ok=True)


def watchdog_disarm_age_seconds(proj, claude_session_id):
    """Seconds since the disarm marker's `ts`, or None if the marker is absent or unparseable
    (a corrupt/missing ts must NEVER read as 'old enough to tear down' -- fail safe)."""
    f = watchdog_disarm_file(proj, claude_session_id)
    if not (f and f.exists()):
        return None
    try:
        raw = json.loads(f.read_text())
        ts = datetime.datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()


def watchdog_disarm_deadline_passed(proj, claude_session_id):
    """True iff the disarm marker exists AND has been idle for at least one interval. Used by
    BOTH the Stop hook (to write teardown_ready) and UserPromptSubmit (the re-arm fallback)."""
    age = watchdog_disarm_age_seconds(proj, claude_session_id)
    if age is None:
        return False
    return age >= watchdog_interval(proj) * 60


def is_watchdog_teardown_ready(proj, claude_session_id):
    f = watchdog_teardown_ready_file(proj, claude_session_id)
    return bool(f and f.exists())


def set_watchdog_teardown_ready(proj, claude_session_id):
    f = watchdog_teardown_ready_file(proj, claude_session_id)
    if f is not None:
        _atomic_write_text(f, "ready\n")


def clear_watchdog_teardown_ready(proj, claude_session_id):
    f = watchdog_teardown_ready_file(proj, claude_session_id)
    if f is not None:
        f.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Tick liveness: is the cron still there at all?
#
# The cron lives only in the host session's memory, so a host restart destroys it. Recreating it
# needs an instruction delivered to the agent, and on a RESUME the host does not deliver the
# SessionStart hook's additionalContext (measured 2026-09-19) -- so nothing re-arms while every
# flag still reports healthy. Rather than detect that host behaviour, detect its consequence:
# ticks stopped arriving. That also covers a hand-deleted cron, the 7-day auto-expiry and a
# crashed scheduler, none of which any flag would reveal either.
# --------------------------------------------------------------------------
def watchdog_tick_seen_file(proj, claude_session_id):
    stem = _watchdog_marker_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".tick_seen")


def set_watchdog_tick_seen(proj, claude_session_id):
    """Record that the cron is alive at `now`. Written when a tick actually arrives, and when an
    arm is requested (asking counts as liveness for one window, so a long busy stretch -- which
    legitimately produces no ticks, because a tick only fires while the REPL is idle -- does not
    append an arm block to every prompt). Never written on an ordinary prompt: that would keep the
    marker permanently fresh and mask a dead scheduler forever."""
    f = watchdog_tick_seen_file(proj, claude_session_id)
    if f is None:
        return
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _atomic_write_text(f, json.dumps({"ts": ts, "session_id": str(claude_session_id)}) + "\n")


def clear_watchdog_tick_seen(proj, claude_session_id):
    f = watchdog_tick_seen_file(proj, claude_session_id)
    if f is not None:
        f.unlink(missing_ok=True)


def watchdog_tick_seen_age_seconds(proj, claude_session_id):
    """Seconds since the tick-seen marker's `ts`, or None if absent or unparseable."""
    f = watchdog_tick_seen_file(proj, claude_session_id)
    if not (f and f.exists()):
        return None
    try:
        raw = json.loads(f.read_text())
        ts = datetime.datetime.fromisoformat(str(raw["ts"]).replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=datetime.timezone.utc)
    return (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()


def watchdog_ticks_presumed_dead(proj, claude_session_id):
    """True when no tick has been seen for more than TWO intervals -- or none has ever been seen.

    The fail direction is the OPPOSITE of `watchdog_disarm_deadline_passed`. There, an absent or
    corrupt marker must never read as 'old enough to tear down', because tearing down wrongly
    loses the watchdog. Here, absent or corrupt means no tick was ever observed, and the only
    consequence of being wrong is one redundant arm instruction -- while being wrong the other way
    leaves the watchdog silently dead, which is the whole bug. So unreadable reads as DEAD.

    Two intervals rather than one because a tick only fires while the REPL is idle: a genuinely
    busy session can miss one without anything being wrong."""
    age = watchdog_tick_seen_age_seconds(proj, claude_session_id)
    if age is None:
        return True
    return age > watchdog_interval(proj) * 60 * 2


def claude_session_id_from_env():
    """This Claude session's id from the environment, or None. `CLAUDE_CODE_SESSION_ID` is what
    Claude Code exports (its value equals the transcript stem, the same key the hooks derive via
    claude_session_key); the legacy `CLAUDE_SESSION_ID` is a fallback. None (rather than a guess)
    when neither is set -- used by slash-command scripts that receive only argv."""
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_SESSION_ID"):
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


# --------------------------------------------------------------------------
# per Claude-session codex reviewer session id
# (captured once at session start; resumed BY ID from any worktree)
# --------------------------------------------------------------------------
def claude_session_key(data):
    """Stable key identifying THIS Claude session, for per-session reviewer isolation.
    Prefers the hook payload's `session_id`; falls back to the transcript filename stem
    (also unique per session) so concurrent Claude sessions -- e.g. a background job and the
    main session on the SAME repo -- never share one codex reviewer session. None only if
    neither field is present (then everything shares the 'default' slot)."""
    sid = (data or {}).get("session_id")
    if sid:
        return sid
    tp = (data or {}).get("transcript_path")
    if tp:
        return Path(tp).stem
    return None


# One codex session per (Claude session, ROLE). The stop supervisor runs at EVERY turn end and
# is therefore the most frequent writer of turns into a shared session; on 2026-07-31 a commit
# audit resumed a session whose previous turn was a stop supervision and answered in the stop
# supervisor's framing, blocking a good commit (the gate is fail-closed, so the error always
# lands that way round). Splitting the session by role removes that contamination.
ROLE_REVIEWER = "reviewer"


ROLE_STOP = "stop"


_ROLE_SUFFIX = {ROLE_REVIEWER: ".reviewer", ROLE_STOP: ".stop"}


def _role_suffix(role):
    """Filename suffix for a role.

    An unknown role RAISES rather than falling back to the reviewer: a typo that silently
    resolved to the reviewer slot would reinstate exactly the sharing this split removes, and
    would do it invisibly."""
    try:
        return _ROLE_SUFFIX[role]
    except (KeyError, TypeError):
        raise ValueError("unknown reviewer session role: {!r}".format(role))


# Every filename that used to identify a reviewer thread by host session alone. None of them is
# resumable any more: the file records WHICH conversation a thread belongs to but not WHICH
# repository briefed it, and a thread briefed for repository A must never audit repository B.
#
# `sess_<sid>.codex` is the pre-role-split name; the four that follow are the role-split names,
# which fixed reviewer/stop contamination but still carried no project. Both generations are
# retired rather than migrated: the originating repository cannot be established from the file,
# so adopting one would be a guess, and guessing wrong hands a reviewer the wrong brief.
_LEGACY_SESSION_SUFFIXES = (
    ".codex", ".reviewer.codex", ".stop.codex", ".pi", ".stop.pi",
)


def legacy_session_files(claude_session_id):
    """Every unscoped session file that may still exist for `claude_session_id`."""
    if not claude_session_id:
        return []
    state = reviewer_home() / "state"
    stem = "sess_" + _sanitize(claude_session_id)
    return [state / (stem + suffix) for suffix in _LEGACY_SESSION_SUFFIXES]


def legacy_session_id_file(claude_session_id):
    """The PRE-SPLIT session file, `sess_<sid>.codex`. Kept as a named accessor because the
    retirement path and its regression test both refer to it specifically."""
    if not claude_session_id:
        return None
    return reviewer_home() / "state" / ("sess_" + _sanitize(claude_session_id) + ".codex")


def _project_session_stem(proj, claude_session_id):
    """`<project>.sess_<session>` -- the shared prefix of every per-(project, session) reviewer
    file -- or None when either half is missing.

    Both halves are load-bearing. Without the session half, two concurrent Claude sessions in
    one repository share a reviewer; without the project half, one Claude session that moves
    between repositories audits repository B through the reviewer briefed for repository A. A
    missing half yields no file at all rather than a shared slot: the caller then briefs a
    fresh reviewer for that one call and persists nothing, so isolation holds even where
    continuity cannot."""
    sid = str(claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return None
    return _state_stem(canonical_project_key(proj)) + ".sess_" + _sanitize(sid)


def session_id_file(proj, claude_session_id, role=ROLE_REVIEWER):
    """Where THIS (project, Claude session, role) pair's codex reviewer id lives, or None when
    either identity is missing.

    `proj` is positional and required so a new call site cannot silently reach an unscoped
    slot; the resolution to a canonical project key means the main checkout, every linked
    worktree and any subdirectory share one reviewer, while unrelated repositories never do."""
    suffix = _role_suffix(role)  # before the identifiability check, so a bad role always raises
    stem = _project_session_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + suffix + ".codex")


def write_session_id(proj, claude_session_id, codex_session_id, role=ROLE_REVIEWER):
    f = session_id_file(proj, claude_session_id, role)
    if f is None:
        return
    _atomic_write_text(f, (codex_session_id or "").strip() + "\n")


def write_session_id_if_absent(proj, claude_session_id, codex_session_id, role=ROLE_REVIEWER):
    """Persist ours only if this (project, Claude session, role) has no reviewer yet; return
    the id that WON.

    Two hooks in one Claude session (parallel subagent tool calls, or SessionStart
    overlapping a first commit) can both read no id and both brief. A plain write let the
    second one overwrite the first, so the two hooks audited against DIFFERENT reviewer
    sessions and the first was orphaned mid-review. Compare-and-set makes both converge on
    whichever landed first; the loser's freshly briefed session is simply abandoned.

    The race is per ROLE and per PROJECT. Different roles, and different repositories, are
    different files and cannot lose to each other."""
    f = session_id_file(proj, claude_session_id, role)
    if f is None:  # unidentifiable session or project: never persisted, never shared
        return codex_session_id
    won = _publish_if_absent(f, (codex_session_id or "").strip() + "\n")
    if won is None:  # slot exists but is empty/corrupt -> take it over
        write_session_id(proj, claude_session_id, codex_session_id, role)
        return codex_session_id
    return won.strip() or codex_session_id


def read_session_id(proj, claude_session_id, role=ROLE_REVIEWER):
    f = session_id_file(proj, claude_session_id, role)
    if f is None or not f.exists():
        return None
    val = f.read_text().strip()
    return val or None


def clear_session_id(proj, claude_session_id, role=ROLE_REVIEWER):
    f = session_id_file(proj, claude_session_id, role)
    if f is not None:
        f.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# reviewer scratch: per-generation temp dirs for the codex audit, keyed by reviewer_session_id
# --------------------------------------------------------------------------
# The codex commit reviewer copies the staged tree out to audit it, leaving 1.6-1.8 GB scratch dirs
# behind that were never cleaned up. We scope each reviewer GENERATION's scratch to one dir under the
# system temp base (writable by the `workspace-write` sandbox, unlike the state home which sits
# outside the `-C` workspace), set TMPDIR to it on the audit subprocess, and reclaim it at rotation
# plus a mtime-lease sweep at SessionStart. Keyed by the reviewer_session_id -- known at audit time,
# stable across a session's audits (so scratch is reused within a session), and unique per generation
# (so rotation never deletes a path the NEXT reviewer would reuse). See
# docs/plans/temp_scratch_rotation_cleanup.md.

DEFAULT_REVIEWER_SCRATCH_TTL_S = 12 * 3600         # 12 h lease; a live session renews it each audit
DEFAULT_REVIEWER_DISK_WARN_BYTES = 2 * 1024 ** 3   # 2 GiB


def commit_scratch_root():
    """Root under which every reviewer generation's scratch dir lives. Under the system temp base
    (writable by the codex `workspace-write` sandbox), NOT the state home (outside the `-C`
    workspace). `REVIEWER_SCRATCH_ROOT` overrides it wholesale for tests/operators."""
    env = os.environ.get("REVIEWER_SCRATCH_ROOT")
    if env:
        return Path(env)
    return Path(tempfile.gettempdir()) / "reviewer-agent-scratch"


def scratch_key(reviewer_session_id):
    """Filesystem-safe key for one reviewer session id, or None for a falsy id. Session ids are
    globally unique, so the sanitized id alone keys the dir (the `pi:`/`claude:` prefixes sanitize
    to a safe name)."""
    sid = str(reviewer_session_id or "").strip()
    if not sid:
        return None
    return _sanitize(sid)


def commit_scratch_dir(reviewer_session_id, create=False):
    """`<root>/<scratch_key>` for this reviewer generation, or None for a falsy id. `create=True`
    makes the dir AND renews its mtime lease, so every audit stamps the dir as recently used."""
    key = scratch_key(reviewer_session_id)
    if key is None:
        return None
    d = commit_scratch_root() / key
    if create:
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.utime(d, None)           # renew the lease
        except OSError:
            pass
    return d


def remove_commit_scratch_dir(reviewer_session_id):
    """Best-effort removal of one reviewer generation's scratch dir. Never raises; a falsy id or an
    absent dir is a no-op."""
    d = commit_scratch_dir(reviewer_session_id, create=False)
    if d is not None:
        shutil.rmtree(str(d), ignore_errors=True)


def reviewer_scratch_ttl_s():
    raw = os.environ.get("REVIEWER_SCRATCH_TTL_S")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_REVIEWER_SCRATCH_TTL_S


def sweep_orphan_scratch_dirs(max_age_s=None, protect_keys=()):
    """Reclaim scratch dirs whose mtime LEASE is older than `max_age_s`. Liveness is the dir mtime
    (renewed on each audit), deliberately NOT the backend session-id files -- those persist after a
    session ends (only rotation unlinks them), so a session-file signal would keep dead scratch
    forever. `protect_keys` are never removed regardless of age. Best-effort; never raises."""
    if max_age_s is None:
        max_age_s = reviewer_scratch_ttl_s()
    protect = set(protect_keys or ())
    try:
        entries = list(commit_scratch_root().iterdir())
    except OSError:
        return                          # root absent/unreadable: nothing to sweep
    cutoff = time.time() - max_age_s
    for entry in entries:
        if entry.name in protect:
            continue
        try:
            if not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        shutil.rmtree(str(entry), ignore_errors=True)


def reviewer_disk_warn_bytes():
    raw = os.environ.get("REVIEWER_DISK_WARN_BYTES")
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_REVIEWER_DISK_WARN_BYTES


def _human_bytes(n):
    n = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024:
            return "{} B".format(int(n)) if unit == "B" else "{:.1f} {}".format(n, unit)
        n /= 1024.0
    return "{:.1f} TiB".format(n)


def low_disk_warning():
    """A one-line `[reviewerAgent] low disk ...` string when free space on the scratch root's
    nearest EXISTING ancestor is below the threshold, else None. Never raises -- disk reporting must
    never be able to affect a commit verdict. A threshold of 0 disables the check."""
    try:
        threshold = reviewer_disk_warn_bytes()
        if threshold <= 0:
            return None
        path = commit_scratch_root()
        while not path.exists():
            parent = path.parent
            if parent == path:
                return None
            path = parent
        free = shutil.disk_usage(str(path)).free
        if free < threshold:
            return ("[reviewerAgent] low disk: {} free on {} -- reviewer temp scratch may "
                    "fail".format(_human_bytes(free), path))
        return None
    except Exception:
        return None


def rotate_session(proj, claude_session_id):
    """Retire THIS project's reviewer for THIS Claude session: the next gated action briefs a
    fresh one.

    Returns the list of state paths cleared (empty when there was nothing to clear), so the
    caller can report honestly rather than claiming a rotation that did not happen.

    This is the explicit all-role rotation used by `/reviewerSession rotate`. Scheduled rotation
    uses `retire_commit_reviewer_session()` instead, so it leaves the stopContinue supervisor
    untouched. The explicit command remains useful when a changed standing brief must reach both
    live role sessions immediately.

    Clears BOTH role sessions' codex ids, BOTH role sessions' pi ids, THIS project's
    audited-stage record for this session, and THIS project's saved verdict for it. Both roles,
    because an operator typing `/reviewerSession rotate` means "retire the reviewer for this
    session", not "retire one of two sessions you were never told about". Only this project,
    because the operator typed the command in one repository: rotating a reviewer they are not
    looking at -- possibly mid-review in another window -- is not what they asked for. No
    watermark is cleared, which is what this did before the role split: a rotated-in reviewer
    still receives every operator message, since they are always resent, but sees none marked
    new. The audited record is not needed for correctness --
    staged_change_block() already refuses to use a record whose codex_sid differs, so a fresh
    reviewer performs a full review either way -- but leaving orphaned records behind makes the
    state directory unreadable. The saved verdict IS needed: the capsule names it as "your
    previous verdict", which a freshly briefed reviewer never gave.

    Unscoped legacy files are retired too. They belong to no project that can be established,
    so no project's rotation can be said to spare them, and leaving them behind would leave an
    operator looking at files a rotation was supposed to have cleared.

    Retained history is deliberately NOT pruned. It is a record of what happened, not reviewer
    state, and `/reviewerHistory clear` is the command for purging it.

    A falsy session id or project clears NOTHING. There is no shared slot for either
    (see session_id_file), so nothing is owned, and treating an empty id as a glob would wipe
    every OTHER session's reviewer.
    """
    sid = (claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return []
    cleared = []
    # Reclaim this reviewer's scratch BEFORE its session files are unlinked -- removal is keyed by the
    # reviewer id, which we must read while it is still on disk. Covers whichever backend is live; a
    # bare/None id is a no-op, so this is safe for all three readers.
    for _reader in (read_session_id, read_pi_session, read_claude_session):
        remove_commit_scratch_dir(_reader(proj, sid, ROLE_REVIEWER))
    targets = [
        session_id_file(proj, sid, ROLE_REVIEWER),
        session_id_file(proj, sid, ROLE_STOP),
        pi_session_file(proj, sid, ROLE_REVIEWER),
        pi_session_file(proj, sid, ROLE_STOP),
        claude_session_file(proj, sid, ROLE_REVIEWER),
        claude_session_file(proj, sid, ROLE_STOP),
        audited_stage_path(proj, sid),
        last_review_file(proj, sid),
        reviewer_session_audit_count_file(proj, sid),
        commit_carryover_file(proj, sid),  # a stale marker must not pair with unrelated new findings
    ]
    targets += legacy_session_files(sid)
    for f in targets:
        if f is not None and f.exists():
            try:
                f.unlink()
            except OSError:
                continue
            cleared.append(str(f))
    return cleared


def reviewer_session_audit_count_file(proj, claude_session_id):
    stem = _project_session_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + ".reviewer-audits.json")


@contextmanager
def _reviewer_session_audit_lock():
    lock = reviewer_home() / "state" / ".reviewer-session-audits.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def reviewer_session_completed_audits(proj, claude_session_id, reviewer_session_id):
    f = reviewer_session_audit_count_file(proj, claude_session_id)
    if f is None:
        return 0
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(data, dict) or data.get("reviewer_session") != reviewer_session_id:
        return 0
    try:
        return max(0, int(data.get("count", 0)))
    except (TypeError, ValueError):
        return 0


def _current_reviewer_matches(proj, claude_session_id, reviewer_session_id):
    expected = str(reviewer_session_id or "")
    if expected.startswith("pi:"):
        return read_pi_session(proj, claude_session_id) == expected[3:]
    if expected.startswith("claude:"):
        return read_claude_session(proj, claude_session_id) == expected[7:]
    return read_session_id(proj, claude_session_id) == expected


def retire_commit_reviewer_session(
    proj, claude_session_id, reviewer_session_id, preserve_last_review=False
):
    """Retire only the commit reviewer, preserving the stopContinue supervisor."""
    if not _current_reviewer_matches(proj, claude_session_id, reviewer_session_id):
        return False
    expected = str(reviewer_session_id or "")
    if expected.startswith("pi:"):
        target = pi_session_file(proj, claude_session_id, ROLE_REVIEWER)
    elif expected.startswith("claude:"):
        target = claude_session_file(proj, claude_session_id, ROLE_REVIEWER)
    else:
        target = session_id_file(proj, claude_session_id, ROLE_REVIEWER)
    targets = [
        target,
        audited_stage_path(proj, claude_session_id),
        reviewer_session_audit_count_file(proj, claude_session_id),
    ]
    if not preserve_last_review:
        targets.append(last_review_file(proj, claude_session_id))
    for f in targets:
        if f is not None:
            f.unlink(missing_ok=True)
    if preserve_last_review:
        # The last review (its outstanding findings) survived the unlink above; arm the carryover so
        # the SUCCESSOR's first review inherits and re-verifies them, rather than diverging by
        # amnesia (staged_change_block is otherwise silent on a no-record / session-id mismatch). The
        # marker is NON-consuming: the successor inherits it while fresh and stops once it records
        # its own completed audit; a clean retirement (the else below) discards it.
        set_commit_carryover(proj, claude_session_id)
    else:
        # Clean retirement without preserved findings: discard any stale carryover left by a prior
        # preserved rotation so a fresh successor is not handed findings that no longer apply.
        clear_commit_carryover(proj, claude_session_id)
    remove_commit_scratch_dir(reviewer_session_id)      # reclaim this generation's scratch
    return True


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
    """Count one completed stop review for this (project, session); returns the new count."""
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
    """Retire only the stopContinue supervisor, preserving the commit reviewer. Returns the state
    paths cleared, the review count among them, so the count restarts with the new session."""
    sid = (claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return []
    cleared = []
    for f in (session_id_file(proj, sid, ROLE_STOP), pi_session_file(proj, sid, ROLE_STOP),
              claude_session_file(proj, sid, ROLE_STOP), _stop_review_count_file(proj, sid)):
        if f is not None and f.exists():
            try:
                f.unlink()
            except OSError:
                continue
            cleared.append(str(f))
    return cleared


def record_completed_reviewer_audit(
    proj, claude_session_id, reviewer_session_id, preserve_last_review=False
):
    """Count one completed non-retry audit and rotate at the configured limit."""
    limit = reviewer_session_audit_limit()
    if limit == 0 or not reviewer_session_id:
        return 0, False
    f = reviewer_session_audit_count_file(proj, claude_session_id)
    if f is None:
        return 0, False
    with _reviewer_session_audit_lock():
        if not _current_reviewer_matches(proj, claude_session_id, reviewer_session_id):
            return 0, False
        count = reviewer_session_completed_audits(
            proj, claude_session_id, reviewer_session_id
        ) + 1
        _atomic_write_text(f, json.dumps({
            "reviewer_session": reviewer_session_id,
            "count": count,
        }, indent=2) + "\n")
        if count >= limit:
            return count, retire_commit_reviewer_session(
                proj, claude_session_id, reviewer_session_id,
                preserve_last_review=preserve_last_review,
            )
        return count, False


def audited_stage_path(proj, claude_session_id):
    """Where the audited-stage record for one (project, session) lives.

    Defined here rather than imported from `reviewer_prompts`, which sits ABOVE this module in
    the layer order (`tools/check_split_layering.py`): rotation has to delete the file, and a
    reverse import to learn its name would invert the layering. `reviewer_prompts` owns the
    reading and writing and delegates the path here, so there is still exactly one definition.
    """
    sid = str(claude_session_id or "").strip()
    if not sid or not str(proj or "").strip():
        return None
    return reviewer_home() / "state" / "{}.{}.audited.json".format(
        _state_stem(canonical_project_key(proj)), _sanitize(sid)
    )


def pi_session_file(proj, claude_session_id, role=ROLE_REVIEWER):
    """Pi session slot for one project, Claude session and reviewer role.

    Named with the same `<project>.sess_<session>.<role>` stem as the codex slot and separated
    only by the extension, so the two backends can never resolve to one file. Unknown roles
    raise through ``_role_suffix`` rather than silently falling back to the commit reviewer and
    recreating cross-role prompt contamination.
    """
    suffix = _role_suffix(role)  # validate before the identifiability check
    stem = _project_session_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + suffix + ".pi")


def claude_session_file(proj, claude_session_id, role=ROLE_REVIEWER):
    """Where the `claude` backend's child SESSION ID lives for one project, session and role.

    Unlike codex and pi, this id is not discovered from the child's output: the CLI accepts
    `--session-id <uuid>`, so the harness mints the id, stores it here, and resumes it by name.
    Same stem as the other backends, separated only by the extension, so no two backends can
    ever resolve to one file.
    """
    suffix = _role_suffix(role)  # validate before the identifiability check
    stem = _project_session_stem(proj, claude_session_id)
    if stem is None:
        return None
    return reviewer_home() / "state" / (stem + suffix + ".claude")


def read_claude_session(proj, claude_session_id, role=ROLE_REVIEWER):
    f = claude_session_file(proj, claude_session_id, role)
    if f is None or not f.exists():
        return None
    return f.read_text(encoding="utf-8").strip() or None


def write_claude_session(proj, claude_session_id, session_id, role=ROLE_REVIEWER):
    f = claude_session_file(proj, claude_session_id, role)
    if f is None:
        return
    _atomic_write_text(f, (session_id or "").strip() + "\n")


def write_claude_session_if_absent(proj, claude_session_id, session_id, role=ROLE_REVIEWER):
    """Compare-and-set, for the same reason the other backends have one: two hooks in one
    developer session can both find no id and both mint one, and the loser's child would
    otherwise orphan the winner's conversation."""
    f = claude_session_file(proj, claude_session_id, role)
    if f is None:
        return session_id
    won = _publish_if_absent(f, (session_id or "").strip() + "\n")
    if won is None:
        write_claude_session(proj, claude_session_id, session_id, role)
        return session_id
    return won.strip() or session_id


def read_pi_session(proj, claude_session_id, role=ROLE_REVIEWER):
    f = pi_session_file(proj, claude_session_id, role)
    if f is None or not f.exists():
        return None
    val = f.read_text(encoding="utf-8").strip()
    return val or None


def write_pi_session(proj, claude_session_id, session_id, role=ROLE_REVIEWER):
    f = pi_session_file(proj, claude_session_id, role)
    if f is None:
        return
    _atomic_write_text(f, (session_id or "").strip() + "\n")


def write_pi_session_if_absent(proj, claude_session_id, session_id, role=ROLE_REVIEWER):
    """Compare-and-set counterpart of write_session_id_if_absent, for the pi backend: two
    concurrent first turns each start their own pi session, and the later one must not
    overwrite (and orphan) the session the other one already published."""
    f = pi_session_file(proj, claude_session_id, role)
    if f is None:
        return session_id
    won = _publish_if_absent(f, (session_id or "").strip() + "\n")
    if won is None:
        write_pi_session(proj, claude_session_id, session_id, role)
        return session_id
    return won.strip() or session_id


def session_brief_file(reviewer_session_id):
    sid = str(reviewer_session_id or "").strip()
    return None if not sid else reviewer_home() / "state" / (_sanitize(sid) + ".brief.txt")


def record_session_brief(reviewer_session_id, text):
    """Persist the brief ACTUALLY attached to a codex reviewer session.

    codex receives the brief exactly once, when the session is created; every later call resumes
    the id and re-sends nothing. Anything that reads reviewerAgent.md from the working tree at
    audit time therefore describes a brief the reviewer never saw as soon as that file changes
    mid-session. Written once and never overwritten, for the same reason.

    Keyed by REVIEWER session id, so a `/reviewerSession rotate` -- which mints a new id --
    starts a new brief record without needing to clear this one. Never raises."""
    f = session_brief_file(reviewer_session_id)
    if f is None:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        if not f.exists():
            f.write_text(text or "", encoding="utf-8")
    except OSError:
        pass


def session_brief(reviewer_session_id):
    """The recorded brief for a reviewer session, or None when there is no record -- which is
    the honest answer for a session created before this was captured, and callers must say so
    rather than substituting the current working-tree brief silently."""
    f = session_brief_file(reviewer_session_id)
    if f is None:
        return None
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return None


def last_review_file(proj, claude_session_id=None):
    """Where the most recent verdict for THIS project -- and this Claude session, when it can be
    identified -- is kept.

    Never global. The old single reviews/last_commit_review.txt was overwritten by every review in
    every repository and every concurrent session, and it is the path required_fixes_message
    reports to the agent, so one agent could be handed another's findings. The capsule now NAMES
    this file to the reviewer as its own prior findings, which makes the scope a correctness
    property rather than tidiness.

    An unidentifiable session falls back to the project-scoped name rather than to nothing:
    required_fixes_message must always have a real path to report, and losing the full findings
    list is the worse failure. reviewer_capsule declines to call that file "your previous
    verdict"."""
    stem = _state_stem(canonical_project_key(proj))
    sid = str(claude_session_id or "").strip()
    if sid:
        stem += "." + _sanitize(sid)
    return reviewer_home() / "reviews" / (stem + ".last_review.txt")


# --------------------------------------------------------------------------
# provider rejection of an otherwise well-formed model / effort
#
# is_valid_codex_model and is_valid_backend_effort check SHAPE, which is all a local check can
# do: whether `gpt-5.6-luna` is reachable is a property of the account, and whether `ultra` is
# accepted is a property of the model. Both were observed to fail only at call time -- an
# unreachable model returns 404/403, and an unsupported level returns HTTP 400
# `[reasoning.effort] [invalid_enum_value]`.
#
# Under a fail-CLOSED gate that surfaces as "every commit in this project is blocked", with the
# provider's own sentence buried in a stderr tail and the remedy (`/reviewerModel`) named
# nowhere. These patterns turn that into a diagnosis. They are deliberately specific: sending an
# operator to `/reviewerModel` for a network blip would waste the one message that had to be
# right, so a rate limit, a reset connection and an unrelated crash must NOT match.
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


def model_rejection_detail(backend, text):
    """The provider sentence that says a model or effort was refused, or None.

    Returns the matching line rather than a bare True so the caller can quote the provider
    instead of paraphrasing it -- the exact wording is what an operator searches for."""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _REJECTION_PATTERNS:
            if pattern.search(stripped):
                return stripped
    return None


def model_rejection_message(backend, text):
    """An actionable diagnostic naming the command, the current selection, and its SOURCE.

    The source matters as much as the value: model and effort resolve from an env var, a state
    file written by `/reviewerModel`, or config.json, and an operator told only the value will
    change the wrong one and see no effect."""
    detail = model_rejection_detail(backend, text)
    model, model_source = reviewer_model_detail(backend)
    effort, effort_source = reviewer_effort_detail(backend)
    return (
        "The {} reviewer refused the configured selection: {}\n"
        "Model: {} (from {}). Reasoning effort: {} (from {}).\n"
        "This is a selection problem, not a transient failure: the value is well-formed, but "
        "the provider or account will not run it. Choose a reachable model with "
        "`/reviewerModel <model>`, or a level that model accepts with `/reviewerModel effort "
        "<level>`; `/reviewerModel` alone reports the current selection and where each part "
        "comes from. If the value above came from an environment variable, that variable wins "
        "over the command and has to be changed or unset."
    ).format(
        backend, detail or "(no provider message)",
        model or "the tool default", model_source,
        effort or "the tool default", effort_source,
    )


# --------------------------------------------------------------------------
# provider usage limits
#
# Distinct from a model rejection and needing the OPPOSITE advice: the selection is fine and
# changing it fixes nothing, so `/reviewerModel` is the wrong door. Under a fail-closed gate an
# exhausted quota presents as "every commit in this project is blocked (exit 1)", which is the
# same sentence as a crash -- and the remedy (wait, or review on a different backend) is named
# nowhere. Kept narrow on purpose: these words appear in the provider's own message, and a
# reviewer merely QUOTING them in a verdict never reaches here, because a verdict is stdout and
# this only ever reads the failure stream.
# --------------------------------------------------------------------------
_USAGE_LIMIT_PATTERNS = (
    re.compile(r"usage limit", re.I),
    re.compile(r"\brate.?limit(ed|ing)?\b", re.I),
    re.compile(r"quota[^\n]{0,40}\b(exceeded|exhausted)\b", re.I),
    re.compile(r"\b429\b"),
)


def usage_limit_detail(text):
    """The provider sentence saying a usage limit stopped the run, or None.

    The matching line is returned rather than a bare True: it usually carries the reset time,
    which is the single most useful fact an operator can be given here."""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern in _USAGE_LIMIT_PATTERNS:
            if pattern.search(stripped):
                return stripped
    return None


def usage_limit_message(backend, text):
    """An actionable diagnostic for an exhausted quota, naming what actually helps."""
    detail = usage_limit_detail(text)
    return (
        "The {} reviewer could not run because a usage limit was reached: {}\n"
        "This is a quota problem, not a configuration problem: the reviewer, the model and the "
        "staged diff are all fine, so changing the model with `/reviewerModel` will not help. "
        "Wait for the limit to reset and retry the identical commit -- the reviewer session is "
        "resumed by id, so a later attempt keeps whatever it had already read. To keep working "
        "in the meantime, switch this project to another reviewer backend (REVIEWER_BACKEND, or "
        "\"backend\" in config.json), or run `/reviewerAgent off` to stop gating commits."
    ).format(backend, detail or "(no provider message)")
