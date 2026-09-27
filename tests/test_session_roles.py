# SPDX-License-Identifier: MPL-2.0
"""One codex session per (Claude session, ROLE).

Why this exists: on 2026-07-31 a commit audit came back with a STOP-SUPERVISOR answer -- a
single off-contract line telling the agent to resolve manifest-listed dirty files -- and
blocked a good commit. Every distinctive term in it (`manifest`, `dirty`, `uncommitted`) is
exclusive to STOP_SUPERVISE_PROMPT and absent from both commit prompts, and an immediate retry
with a byte-identical staged set returned a proper REJECTED list. The cause was turn-context
bleed: all four hooks resumed ONE codex session per Claude session, and the stop supervisor --
which runs at every turn end -- is by far its most frequent writer.

The gate is FAIL-CLOSED, so this class of error never waves a bad commit through; it BLOCKS a
good one, at the cost of a paid review round plus the rework behind it.

Design is in SESSION_SPLIT_PLAN.md. Two slots split, and only two:

  * the codex session id -- reviewer keeps `sess_<sid>.codex`, stop gains `sess_<sid>.stop.codex`
  * the operator-message watermark -- reviewer keeps `opmark_<sid>`, stop gains `opmark_<sid>.stop`

Everything else is already safe and is pinned as such below.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import reviewer_lib as rl

PROJ = "/Users/x/proj"

_BASE = Path(__file__).resolve().parent.parent
ROOT = _BASE / "scripts" if (_BASE / "scripts").is_dir() else _BASE
STOP = str(ROOT / "stop_hook.py")
FAKE = str(Path(__file__).resolve().parent / "fake_codex.py")


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.delenv("REVIEWER_OPERATOR_TRANSCRIPT", raising=False)
    return tmp_path


def state_dir():
    return rl.reviewer_home() / "state"


def _repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _typed(text, uuid, ts="2026-07-31T10:00:00.000Z"):
    """A genuinely typed operator message -- the real shape, per test_operator_transcript."""
    return {
        "type": "user", "userType": "external", "isSidechain": False,
        "origin": {"kind": "human"}, "promptSource": "typed",
        "uuid": uuid, "timestamp": ts, "version": "2.1.219",
        "message": {"role": "user", "content": text},
    }


def _transcript(tmp_path, *events):
    p = tmp_path / "transcript.jsonl"
    p.write_text("".join(json.dumps(e) + "\n" for e in events))
    rl._reset_transcript_cache()
    return str(p)


# --------------------------------------------------------------------------
# 1. the session id splits by role
# --------------------------------------------------------------------------
def _scoped(name):
    """`state/<project-stem>.sess_<session><suffix>` -- the whole naming mechanism, spelled out.

    The project stem leads: a reviewer thread belongs to one repository, and the pre-project
    names (`sess_<sid>.codex`, and the role-split `sess_<sid>.<role>.codex`) could not say
    which. The role part follows, because a session created before the role split served every
    gate and already holds stop-supervisor turns.
    """
    return state_dir() / (rl._state_stem(rl.canonical_project_key(PROJ)) + "." + name)


def test_both_role_paths_by_literal_filename():
    assert rl.session_id_file(PROJ, "claude-1") == _scoped("sess_claude-1.reviewer.codex")
    assert (rl.session_id_file(PROJ, "claude-1", role="reviewer")
            == _scoped("sess_claude-1.reviewer.codex"))
    assert (rl.session_id_file(PROJ, "claude-1", role="stop")
            == _scoped("sess_claude-1.stop.codex"))


def test_an_unknown_role_raises_rather_than_falling_back():
    # A typo that silently resolved to the reviewer slot would reinstate exactly the sharing this
    # split removes, and would do it invisibly.
    for bad in ("reviewr", "STOP", "", None):
        with pytest.raises(ValueError):
            rl.session_id_file(PROJ, "claude-1", role=bad)
        with pytest.raises(ValueError):
            rl.operator_watermark_file(PROJ, "claude-1", role=bad)


def test_a_pre_split_session_is_never_resumed(tmp_path, monkeypatch):
    """The regression test for the legacy carry-over.

    Plant a pre-split `sess_<sid>.codex` -- the shared session that already contains
    stop-supervisor turns -- and require ensure_session to brief a FRESH reviewer instead of
    returning it. Without this the split would only protect sessions started after deployment,
    while the sessions in flight AT deployment, the ones actually at risk, kept their
    contaminated conversation.
    """
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "post-split")
    repo = _repo(tmp_path / "repo_legacy")
    state_dir().mkdir(parents=True, exist_ok=True)
    legacy = state_dir() / "sess_claude-legacy.codex"
    legacy.write_text("contaminated-sid\n")

    sid, err = rl.ensure_session(str(repo), "claude-legacy")

    assert err == ""
    assert sid == "post-split"
    assert rl.read_session_id(str(repo), "claude-legacy") == "post-split"
    # retired, not merely ignored, so state/ does not keep an id nothing uses
    assert not legacy.exists()


def test_stop_and_reviewer_session_ids_are_stored_independently():
    rl.write_session_id(PROJ, "claude-2", "reviewer-sid")
    rl.write_session_id(PROJ, "claude-2", "stop-sid", role="stop")
    assert rl.read_session_id(PROJ, "claude-2") == "reviewer-sid"
    assert rl.read_session_id(PROJ, "claude-2", role="stop") == "stop-sid"


def test_writing_the_stop_id_never_disturbs_the_reviewer_id():
    rl.write_session_id(PROJ, "claude-3", "reviewer-sid")
    rl.write_session_id(PROJ, "claude-3", "stop-sid", role="stop")
    rl.clear_session_id(PROJ, "claude-3", role="stop")
    assert rl.read_session_id(PROJ, "claude-3") == "reviewer-sid"
    assert rl.read_session_id(PROJ, "claude-3", role="stop") is None


def test_compare_and_set_converges_per_role():
    # Two hooks in ONE role must still converge on whichever landed first; the other role's
    # slot is simply a different file and cannot be the loser.
    rl.write_session_id(PROJ, "claude-4", "first")
    assert rl.write_session_id_if_absent(PROJ, "claude-4", "second") == "first"
    assert rl.write_session_id_if_absent(PROJ, "claude-4", "mine", role="stop") == "mine"
    assert rl.read_session_id(PROJ, "claude-4") == "first"
    assert rl.read_session_id(PROJ, "claude-4", role="stop") == "mine"


def test_an_unidentifiable_session_persists_no_stop_id_either():
    # The no-shared-slot rule holds for the new role: a "default" stop slot would let every
    # unidentifiable session in every project resume one another's stop supervisor.
    assert rl.session_id_file(PROJ, None, role="stop") is None
    rl.write_session_id(PROJ, None, "stop-X", role="stop")
    assert rl.read_session_id(PROJ, None, role="stop") is None
    assert not (state_dir() / "sess_default.stop.codex").exists()


def test_ensure_session_briefs_into_the_requested_role(tmp_path, monkeypatch):
    monkeypatch.setenv("REVIEWER_CODEX_BIN", FAKE)
    monkeypatch.setenv("FAKE_CODEX_SESSION_ID", "stop-fresh")
    repo = _repo(tmp_path / "repo_role")
    sid, err = rl.ensure_session(str(repo), "claude-5", role="stop")
    assert err == ""
    assert sid == "stop-fresh"
    assert rl.read_session_id(str(repo), "claude-5", role="stop") == "stop-fresh"
    # and the reviewer slot was NOT filled by the stop brief
    assert rl.read_session_id(str(repo), "claude-5") is None


# --------------------------------------------------------------------------
# 2. the operator-message watermark splits with it
# --------------------------------------------------------------------------
def test_the_reviewer_watermark_shares_the_reviewer_stem():
    assert rl.operator_watermark_file(PROJ, "claude-6") == _scoped("sess_claude-6.opmark")
    assert (rl.operator_watermark_file(PROJ, "claude-6", role="reviewer")
            == _scoped("sess_claude-6.opmark"))


def test_the_stop_watermark_gets_its_own_path():
    assert (rl.operator_watermark_file(PROJ, "claude-6", role="stop")
            == _scoped("sess_claude-6.opmark.stop"))


def test_an_unidentifiable_session_shares_no_stop_watermark():
    rl.write_operator_watermark(PROJ, None, "uuid-1", role="stop")
    assert rl.read_operator_watermark(PROJ, None, role="stop") is None
    assert not (state_dir() / "opmark_None.stop").exists()


def test_both_roles_are_told_the_same_message_is_new(tmp_path):
    """THE regression test for the shared watermark.

    The sequence matters. With NO watermark, `since` is None and operator_transcript_block
    marks nothing -- correctly, because a role that has never composed has no "last review".
    So both marks must be established BEFORE the message under test arrives.

    Against a single shared `opmark_<sid>`, step 3 advances the mark past message 2 and step 4
    renders it unmarked: the stop supervisor runs at every turn end and the commit gate only on
    commits, so the stop role wins that race essentially every time and the commit reviewer --
    a genuinely different codex session that never saw the message -- is shown a fresh operator
    authorization as old context.
    """
    proj = str(_repo(tmp_path / "repo_wm"))
    # 1. establish BOTH marks against a transcript of one message
    path = _transcript(tmp_path, _typed("first", uuid="u1"))
    rl.apply_context("AUDIT", proj, transcript_path=path, claude_session_id="s1")
    rl.apply_context("SUPERVISE", proj, transcript_path=path, claude_session_id="s1",
                     role="stop")
    # 2. the operator says something new
    path = _transcript(tmp_path, _typed("first", uuid="u1"), _typed("AUTHORIZED", uuid="u2"))
    # 3. the stop supervisor fires first, as it does at every turn end
    stop_prompt = rl.apply_context("SUPERVISE", proj, transcript_path=path,
                                   claude_session_id="s1", role="stop")
    # 4. then the commit gate runs
    rl._reset_transcript_cache()
    review_prompt = rl.apply_context("AUDIT", proj, transcript_path=path,
                                     claude_session_id="s1")

    # 5. BOTH must see it marked new
    for label, prompt in (("stop", stop_prompt), ("reviewer", review_prompt)):
        marked = [ln for ln in prompt.splitlines() if "NEW" in ln]
        assert any("AUTHORIZED" in ln for ln in marked), (label, marked)
        assert not any("first" in ln for ln in marked), (label, marked)


def test_each_role_advances_only_its_own_watermark(tmp_path):
    proj = str(_repo(tmp_path / "repo_wm2"))
    path = _transcript(tmp_path, _typed("one", uuid="u1"))
    rl.apply_context("SUPERVISE", proj, transcript_path=path, claude_session_id="s2",
                     role="stop")
    assert rl.read_operator_watermark(proj, "s2", role="stop") == "u1"
    assert rl.read_operator_watermark(proj, "s2") is None

    rl._reset_transcript_cache()
    rl.apply_context("AUDIT", proj, transcript_path=path, claude_session_id="s2")
    assert rl.read_operator_watermark(proj, "s2") == "u1"


# --------------------------------------------------------------------------
# 3. rotation retires the whole Claude session, not one role of it
# --------------------------------------------------------------------------
def test_rotate_clears_both_roles_for_both_backends():
    # An operator typing `/reviewerSession rotate` means "retire the reviewer for this
    # session", not "retire one of two sessions you never told me about".
    rl.write_session_id(PROJ, "claude-7", "reviewer-sid")
    rl.write_session_id(PROJ, "claude-7", "stop-sid", role="stop")
    rl.write_pi_session(PROJ, "claude-7", "pi-reviewer-sid")
    rl.write_pi_session(PROJ, "claude-7", "pi-stop-sid", role=rl.ROLE_STOP)

    state_dir().mkdir(parents=True, exist_ok=True)
    legacy = state_dir() / "sess_claude-7.codex"
    legacy.write_text("pre-split-sid\n")

    cleared = rl.rotate_session(PROJ, "claude-7")

    assert rl.read_session_id(PROJ, "claude-7") is None
    assert rl.read_session_id(PROJ, "claude-7", role="stop") is None
    assert rl.read_pi_session(PROJ, "claude-7") is None
    assert rl.read_pi_session(PROJ, "claude-7", role="stop") is None
    assert not legacy.exists()
    assert str(_scoped("sess_claude-7.reviewer.codex")) in cleared, cleared
    assert str(_scoped("sess_claude-7.stop.codex")) in cleared, cleared
    assert str(_scoped("sess_claude-7.reviewer.pi")) in cleared, cleared
    assert str(_scoped("sess_claude-7.stop.pi")) in cleared, cleared
    assert str(legacy) in cleared, cleared


def test_rotate_clears_no_watermark():
    # Pins CURRENT behaviour so the role split does not quietly change it: rotation clears no
    # watermark today. A rotated-in reviewer still receives every operator message, because
    # they are always resent, but sees none marked new.
    rl.write_session_id(PROJ, "claude-8", "reviewer-sid")
    rl.write_operator_watermark(PROJ, "claude-8", "uuid-1")
    rl.write_operator_watermark(PROJ, "claude-8", "uuid-1", role="stop")

    rl.rotate_session(PROJ, "claude-8")

    assert rl.read_operator_watermark(PROJ, "claude-8") == "uuid-1"
    assert rl.read_operator_watermark(PROJ, "claude-8", role="stop") == "uuid-1"


# --------------------------------------------------------------------------
# 4. each hook uses its own role -- asserted by DRIVING the hook, not reading source
# --------------------------------------------------------------------------
def _stop_input(cwd, transcript_path, session_id="claude-hook"):
    return {
        "cwd": cwd,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }


def test_the_stop_hook_uses_the_stop_session_and_leaves_the_reviewer_alone(tmp_path):
    home_dir = str(rl.reviewer_home())
    repo = str(_repo(tmp_path / "repo_hook"))
    rl.set_stopcontinue_active(repo)
    tr = tmp_path / "stop_transcript.jsonl"
    tr.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "This is a reasonable stopping point."}]},
    }) + "\n")

    env = dict(os.environ)
    env.update({
        "REVIEWER_HOME": home_dir,
        "REVIEWER_CODEX_BIN": FAKE,
        "FAKE_CODEX_SESSION_ID": "hook-stop-sid",
        "FAKE_CODEX_STDOUT": "Continue: finish the seams.",
        "FAKE_CODEX_EXIT": "0",
    })
    p = subprocess.run([sys.executable, STOP], input=json.dumps(_stop_input(repo, str(tr))),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr

    assert rl.read_session_id(repo, "claude-hook", role="stop") == "hook-stop-sid"
    assert rl.read_session_id(repo, "claude-hook") is None
    assert not (state_dir() / "sess_claude-hook.codex").exists()


# --------------------------------------------------------------------------
# 5. Pi uses the same role isolation as Codex
# --------------------------------------------------------------------------
def test_pi_reviewer_and_stop_sessions_are_stored_independently():
    assert rl.pi_session_file(PROJ, "claude-9") == _scoped("sess_claude-9.reviewer.pi")
    assert (rl.pi_session_file(PROJ, "claude-9", role=rl.ROLE_STOP)
            == _scoped("sess_claude-9.stop.pi"))

    rl.write_pi_session(PROJ, "claude-9", "pi-reviewer")
    rl.write_pi_session(PROJ, "claude-9", "pi-stop", role=rl.ROLE_STOP)

    assert rl.read_pi_session(PROJ, "claude-9") == "pi-reviewer"
    assert rl.read_pi_session(PROJ, "claude-9", role=rl.ROLE_STOP) == "pi-stop"


def test_pi_session_rejects_an_unknown_role():
    with pytest.raises(ValueError):
        rl.pi_session_file(PROJ, "claude-9", role="STOP")


def test_context_provenance_takes_no_role():
    # It never reads the watermark -- it reports relay/op_msgs/chars/cutoff/last/directives,
    # which the role split cannot change -- so it must not grow an argument implying otherwise.
    with pytest.raises(TypeError):
        rl.context_provenance("/tmp/x", None, "claude-9", role="stop")


# --------------------------------------------------------------------------
# 6. /reviewerSession has to SAY there are two sessions now
# --------------------------------------------------------------------------
TOGGLE = str(ROOT / "reviewer_session_toggle.py")


def _run_toggle(args, session_id, proj=None):
    env = dict(os.environ)
    env["REVIEWER_HOME"] = str(rl.reviewer_home())
    env["CLAUDE_CODE_SESSION_ID"] = session_id
    # The command reports and rotates state for the project it is invoked in, so the test has
    # to name the same project it seeded rather than inheriting the suite's own checkout.
    env["CLAUDE_PROJECT_DIR"] = proj or PROJ
    env.pop("REVIEWER_CONFIG_FILE", None)
    return subprocess.run([sys.executable, TOGGLE] + args,
                          capture_output=True, text=True, env=env)


def test_reviewerSession_reports_an_established_stop_slot():
    rl.write_session_id(PROJ, "claude-tog", "reviewer-sid")
    rl.write_session_id(PROJ, "claude-tog", "stop-sid", role="stop")
    p = _run_toggle([], "claude-tog")
    assert p.returncode == 0, p.stderr
    assert "stop-sid" in p.stdout, p.stdout
    assert "reviewer-sid" in p.stdout, p.stdout


def test_reviewerSession_says_the_stop_slot_is_briefed_lazily_when_absent():
    # The branch that tells an operator an ABSENT stop slot is normal rather than broken.
    rl.write_session_id(PROJ, "claude-tog2", "reviewer-sid")
    p = _run_toggle([], "claude-tog2")
    assert p.returncode == 0, p.stderr
    low = p.stdout.lower()
    assert "stopcontinue" in low, p.stdout
    assert "lazily" in low, p.stdout


def test_reviewerSession_reports_pi_reviewer_and_pi_stop_slots(monkeypatch):
    monkeypatch.setenv("REVIEWER_BACKEND", "pi")
    rl.write_pi_session(PROJ, "claude-pi-tog", "pi-reviewer")
    rl.write_pi_session(PROJ, "claude-pi-tog", "pi-stop", role=rl.ROLE_STOP)

    p = _run_toggle([], "claude-pi-tog")

    assert p.returncode == 0, p.stderr
    assert "pi reviewer pi-reviewer" in p.stdout
    assert "pi stopContinue supervisor pi-stop" in p.stdout


def test_the_session_brief_record_needs_no_role():
    # Keyed by the CODEX session id, not the Claude session id, so two roles get two brief
    # records with no change at all.
    assert rl.session_brief_file("stop-sid") != rl.session_brief_file("reviewer-sid")


def test_the_stop_session_is_retired_after_n_reviews(tmp_path, monkeypatch):
    # Task 12: every stop resends the operator transcript into one RESUMED stop session, so its
    # context grew from 2.5k to 152k tokens. Retiring it every N reviews bounds that cost.
    monkeypatch.setenv("REVIEWER_HOME", str(tmp_path / "rh"))
    monkeypatch.setenv("REVIEWER_STOP_ROTATE_EVERY", "3")
    proj, sid = str(tmp_path), "S"
    rl.write_session_id_if_absent(proj, sid, "STOP-1", role=rl.ROLE_STOP)
    rl.write_session_id_if_absent(proj, sid, "REV-1", role=rl.ROLE_REVIEWER)
    assert rl.stop_rotate_every() == 3
    assert [rl.record_stop_review(proj, sid) for _ in range(3)] == [1, 2, 3]
    cleared = rl.retire_stop_session(proj, sid)
    assert cleared and rl.read_session_id(proj, sid, rl.ROLE_STOP) is None
    assert rl.read_session_id(proj, sid, rl.ROLE_REVIEWER) == "REV-1"  # commit reviewer kept
    assert rl.record_stop_review(proj, sid) == 1  # the count restarts
