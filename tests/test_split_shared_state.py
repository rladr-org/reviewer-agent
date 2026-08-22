# SPDX-License-Identifier: MPL-2.0
"""Mutable state relocated by the split must stay ONE object, not a facade copy.

LIB_SPLIT_PLAN.md 5.1. `reviewer_base` (step 1) exported only a constant int, so step 2 is the
first time the split moves a mutable container: `_TRANSCRIPT_CACHE`, written on every transcript
read and cleared between simulated audits.

The failure this guards against is an ALIAS divergence, and its shape was measured on a
two-module probe rather than assumed, because the obvious guess is wrong. Rebinding the name in
the layer (`global _TRANSCRIPT_CACHE; _TRANSCRIPT_CACHE = {}` -- a natural way to write a reset)
leaves the layer's OWN functions correct, since they resolve the name in their own module
globals and so read the new dict. What goes stale is the facade's variable: `reviewer_lib` keeps
pointing at the old object.

No hook or test reads the facade's variable directly today, and no code left in
`reviewer_lib.py` does either -- every reader moved with it. So this is a guard against a future
direct reader, not a live defect, and its whole value is that it fails at the moment a rebinding
is introduced rather than whenever someone later adds the reader it would break.

The rows below hold ACCESSORS rather than attribute NAMES. `getattr(rl, name)` over a parametrized
variable would reach the facade in a way `tools/gen_facade_contract.py` cannot pin -- it reported
exactly that against the first draft of this file -- and a name the contract cannot pin is a name
the split can silently drop. A lambda closing over a literal `rl.X` keeps the access visible to
the AST scan.

Every later step that relocates mutable module state adds a row; `_LAST_INVOCATION` reaches
`reviewer_backends` at step 6.
"""
import pytest

import reviewer_lib as rl
import reviewer_manifest
import reviewer_state
import reviewer_transcript

#: (label, facade accessor, layer accessor, the writer most likely to be rewritten as a
#: rebinding -- or None if the container has no such writer yet).
#:
#: NOT every mutable container the split relocates: only those something WRITES. Read-only
#: lookup tables (`_ROLE_SUFFIX`, the git commit-argument tables) are mutable types but have no
#: writer, so a row for them would assert nothing. And `_LAST_INVOCATION` is deliberately absent
#: for the opposite reason -- it is REBOUND under `global` on every `_run` by design, so an
#: identity row would fail as soon as anything ran. It is not re-exported at all; see
#: LIB_SPLIT_PLAN.md 5.6.
RELOCATED_STATE = [
    pytest.param(
        "reviewer_transcript._TRANSCRIPT_CACHE",
        lambda: rl._TRANSCRIPT_CACHE,
        lambda: reviewer_transcript._TRANSCRIPT_CACHE,
        rl._reset_transcript_cache,
        id="transcript-cache",
    ),
    pytest.param(
        "reviewer_state._MIGRATED_CONTROL_STEMS",
        lambda: rl._MIGRATED_CONTROL_STEMS,
        lambda: reviewer_state._MIGRATED_CONTROL_STEMS,
        None,  # no reset writer: `_migrate_project_control_state` only ever adds
        id="migrated-control-stems",
    ),
    pytest.param(
        # The one row guarding a LIVE direct reader: tests/test_reviewer_lib.py:939 and :954
        # call `rl._TOPLEVEL_CACHE.clear()` on the facade itself, which is why the name is in
        # the contract. `.clear()` mutates the shared object and so survives the move; a
        # rebinding would leave those tests clearing a dict `git_toplevel` no longer reads.
        "reviewer_manifest._TOPLEVEL_CACHE",
        lambda: rl._TOPLEVEL_CACHE,
        lambda: reviewer_manifest._TOPLEVEL_CACHE,
        None,  # no reset FUNCTION; the tests clear it directly
        id="toplevel-cache",
    ),
]

_TOKEN = "__split_shared_state_probe__"


# The containers are not all one shape -- `_TRANSCRIPT_CACHE` and `_TOPLEVEL_CACHE` are dicts,
# `_MIGRATED_CONTROL_STEMS` is a set -- and the set API diverges exactly where a naive probe
# touches it: `container[k] = v` raises `TypeError: 'set' object does not support item
# assignment`, and `.pop(k, None)` raises `set.pop() takes no arguments (2 given)`. Membership
# is the one operation that reads the same for both.
def _insert(container, token):
    if isinstance(container, dict):
        container[token] = token
    else:
        container.add(token)


def _discard(container, token):
    if isinstance(container, dict):
        container.pop(token, None)
    else:
        container.discard(token)


@pytest.mark.parametrize("label,facade,layer,reset", RELOCATED_STATE)
def test_the_facade_shares_the_layers_object_rather_than_a_copy(label, facade, layer, reset):
    assert facade() is layer(), (
        "{} has been REBOUND rather than mutated, so the reviewer_lib name is now a stale "
        "alias for a different object".format(label))


@pytest.mark.parametrize("label,facade,layer,reset", RELOCATED_STATE)
def test_a_mutation_through_the_layer_is_visible_through_the_facade(label, facade, layer, reset):
    """Identity is the mechanism; this is the consequence that actually matters."""
    _insert(layer(), _TOKEN)
    try:
        assert _TOKEN in facade()
    finally:
        _discard(layer(), _TOKEN)


@pytest.mark.parametrize("label,facade,layer,reset", RELOCATED_STATE)
def test_the_reset_writer_clears_the_object_instead_of_replacing_it(label, facade, layer, reset):
    """The row with teeth: a reset rewritten as a rebinding passes both tests above -- they run
    before it -- and fails only here. Every existing caller of `_reset_transcript_cache` reaches
    it through the facade (tests/test_operator_transcript.py:445, tests/test_session_roles.py:67),
    which is precisely the path that would keep working while the alias diverged."""
    if reset is None:
        pytest.skip("{} has no reset writer".format(label))
    before = layer()
    _insert(before, _TOKEN)
    reset()
    assert _TOKEN not in layer()
    assert layer() is before, "the reset replaced the container instead of clearing it"
    assert facade() is layer()
