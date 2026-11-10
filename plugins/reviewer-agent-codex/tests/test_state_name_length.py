# SPDX-License-Identifier: MPL-2.0
"""State and temporary file names stay within the 255-byte file name limit.

See docs/plans/state_name_length_bound.md (repository root). The atomic writers used to name
their temporary sibling after the target, 38 bytes longer, and the project stem kept the whole
repository folder name, so a long folder name pushed state names over the limit."""
import os
import subprocess

import pytest

from reviewer_codex import liveness, state

NAME_MAX = 255
STEM_READABLE_MAX = 165  # 255 - "-" and 20-character digest (21) - ".<64 hex>.txt" (69)
SESSION = "019a1b2c-3d4e-7f50-8a6b-9c0d1e2f3a4b"


def _repo_named(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _size(name):
    return len(os.fsencode(name))


# --------------------------------------------------------------------------
# the atomic writers
# --------------------------------------------------------------------------
@pytest.mark.parametrize("size", [228, NAME_MAX])
def test_the_state_writer_publishes_a_long_but_valid_name(tmp_path, size):
    target = tmp_path / ("n" * size)
    state._atomic_write(target, "content\n")
    assert target.read_text(encoding="utf-8") == "content\n"
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]  # no temporary left


@pytest.mark.parametrize("size", [228, NAME_MAX])
def test_the_liveness_writer_publishes_a_long_but_valid_name(tmp_path, size):
    target = tmp_path / ("n" * size)
    liveness._atomic_json(target, {"ok": True})
    assert target.read_text(encoding="utf-8") == '{"ok": true}'
    assert sorted(p.name for p in tmp_path.iterdir()) == [target.name]


# --------------------------------------------------------------------------
# the project stem
# --------------------------------------------------------------------------
def test_a_long_folder_name_gives_a_bounded_stable_stem(tmp_path):
    repo = _repo_named(tmp_path, "r" * 250)
    stem = state._project_stem(str(repo))
    assert _size(stem) <= STEM_READABLE_MAX + 21
    assert stem == state._project_stem(str(repo))
    assert stem.startswith("r" * STEM_READABLE_MAX + "-")


def test_long_folder_names_that_share_a_prefix_get_different_stems(tmp_path):
    first = _repo_named(tmp_path, "r" * 200 + "a")
    second = _repo_named(tmp_path, "r" * 200 + "b")
    assert state._project_stem(str(first)) != state._project_stem(str(second))


def test_a_folder_name_at_the_bound_keeps_its_whole_name(tmp_path):
    name = "r" * STEM_READABLE_MAX
    stem = state._project_stem(str(_repo_named(tmp_path, name)))
    assert stem.rsplit("-", 1)[0] == name


def test_a_multibyte_folder_name_is_trimmed_on_bytes_at_a_character_boundary(tmp_path):
    repo = _repo_named(tmp_path, "é" * 120)  # 240 bytes
    readable = state._project_stem(str(repo)).rsplit("-", 1)[0]
    assert readable == "é" * (STEM_READABLE_MAX // 2)


def test_feature_flags_round_trip_for_a_long_folder_name(tmp_path, isolated_state):
    repo = str(_repo_named(tmp_path, "r" * 250))
    for feature in ("reviewer", "stop"):
        assert not state.feature_enabled(repo, feature)
        state.set_feature(repo, feature)
        assert state.feature_enabled(repo, feature)
        assert state.enabled_feature_roots(feature) == [state.project_root(repo)]


# --------------------------------------------------------------------------
# every name built from the project stem
# --------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["r" * 250, "é" * 120], ids=["ascii", "two-byte"])
def test_every_stem_keyed_name_fits_the_file_name_limit(tmp_path, isolated_state, name):
    # The stem is the only part of a Codex state name taken from unbounded input; session and
    # project tokens elsewhere are fixed-length digests.
    repo = str(_repo_named(tmp_path, name))
    paths = [
        state._flag(repo, "reviewer"),
        state._flag(repo, "stop"),
        state.risk_acceptance_file(repo),
        state._deferred_stop_file(repo),
        state.last_review_file(repo, SESSION),
        state.review_history_dir(repo),
    ]
    assert all(p is not None for p in paths)
    too_long = [(_size(p.name), p.name) for p in paths if _size(p.name) > NAME_MAX]
    assert not too_long, too_long
