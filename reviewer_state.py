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
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

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


def rotate_session(proj, claude_session_id):
    """Retire THIS project's reviewer for THIS Claude session: the next gated action briefs a
    fresh one.

    Returns the list of state paths cleared (empty when there was nothing to clear), so the
    caller can report honestly rather than claiming a rotation that did not happen.

    Manual only, and deliberately so. Automatic rotation was measured and rejected: the FIRST
    commit review in a session costs a median 93k tokens against a flat ~40k steady state from
    turn 3 out past turn 41, so any automatic trigger would buy a cold start every time it fired
    (REVIEW_COST_REDUCTION_PLAN.md A1.3). What this IS for: the brief is sent once per session,
    so a session briefed before a REVIEWER_STANDING_BRIEF change keeps the old instructions until
    it is retired, and for a reviewer whose accumulated context is no longer useful.

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
    targets = [
        session_id_file(proj, sid, ROLE_REVIEWER),
        session_id_file(proj, sid, ROLE_STOP),
        pi_session_file(proj, sid, ROLE_REVIEWER),
        pi_session_file(proj, sid, ROLE_STOP),
        claude_session_file(proj, sid, ROLE_REVIEWER),
        claude_session_file(proj, sid, ROLE_STOP),
        audited_stage_path(proj, sid),
        last_review_file(proj, sid),
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
