# SPDX-License-Identifier: MPL-2.0
"""What the gate is allowed to commit on the agent's behalf.

Once the gate performs the commit itself and denies the tool call, two things have to be
recovered from that call and nothing else may be guessed at: the message, and the handful of
flags that can be carried. Everything here is specified in
docs/plans/claude_commit_transaction_design.md.

The rules these tests pin, and why each exists:

  * ONE invocation per call. Denying the call drops whatever else was in it, so
    `git commit -m x && git push` would publish the commit and silently skip the push.
  * The message must be what the SHELL would have produced. The hook sees the unexpanded
    token, so `-m "$MSG"` would commit the literal text `$MSG`.
  * The flag set is pinned to the one the Codex implementation accepts, so it cannot grow
    into open-ended option support one sympathetic case at a time.
"""
import pytest

import reviewer_lib as rl


def _request(command):
    request, error = rl.parse_commit_request(command)
    assert error is None, "expected {!r} to be accepted, refused with: {}".format(command, error)
    return request


def _refusal(command):
    request, error = rl.parse_commit_request(command)
    assert request is None, "expected {!r} to be refused, it was accepted".format(command)
    assert error, "a refusal must carry a reason the agent can act on"
    return error


# --------------------------------------------------------------------------
# Exactly one invocation
# --------------------------------------------------------------------------
def test_a_plain_commit_is_the_accepted_shape():
    assert _request("git commit -m hello")["message"] == "hello"


def test_anything_beyond_the_single_commit_is_refused():
    # The gate publishes and then denies the call, so a second command in that call would be
    # dropped without ever running -- the commit lands and the push never happens.
    for command, dropped in (
        ("git commit -m x" + " && " + "git push", "git push"),
        ("git add f.py" + " && " + "git commit -m x", "git add"),
        ("git commit -m x" + " && " + "git commit -m y", "second"),
        ("git commit -m x | tee log.txt", "tee"),
        ("git commit -m x > out.txt", "redirection"),
        ("git status; git commit -m x", "git status"),
    ):
        error = _refusal(command)
        assert "one" in error.lower() or "only" in error.lower(), command


def test_the_refusal_names_what_it_found_so_the_agent_can_split_the_call():
    error = _refusal("git commit -m x" + " && " + "git push")
    assert "push" in error


# --------------------------------------------------------------------------
# The message
# --------------------------------------------------------------------------
def test_repeated_message_flags_join_as_paragraphs_like_git():
    assert _request("git commit -m first -m second")["message"] == "first\n\nsecond"


def test_the_long_and_attached_message_spellings_are_carried():
    assert _request("git commit --message hello")["message"] == "hello"
    assert _request("git commit --message=hello")["message"] == "hello"


def test_a_quoted_message_keeps_its_literal_characters():
    # The whole point of carrying the message as an argument vector rather than a shell string.
    assert _request("git commit -m 'cost $5'")["message"] == "cost $5"
    assert _request("git commit -m 'fix *.txt and ~/home and {a,b}'")["message"] == \
        "fix *.txt and ~/home and {a,b}"
    assert _request('git commit -m "cost \\$5"')["message"] == "cost $5"
    assert _request('git commit -m "two words"')["message"] == "two words"


def test_a_message_the_shell_would_have_expanded_is_refused():
    # The hook cannot know the value without running the shell, and committing the literal
    # token would silently write the wrong message.
    for command in (
        'git commit -m "$MSG"',
        'git commit -m "prefix $MSG"',
        'git commit -m "$(generate_message)"',
        "git commit -m \"`generate_message`\"",
        'git commit -m "${MSG}"',
    ):
        error = _refusal(command)
        assert "expan" in error.lower() or "literal" in error.lower(), command


def test_a_bare_word_message_is_refused_when_it_carries_any_special_character():
    # Stated by quoting context, not by listing expansions: a bare word is accepted only when
    # nothing in it could mean anything to a shell.
    for command in (
        "git commit -m hello*world",
        "git commit -m ~home",
        "git commit -m fix{a,b}",
        "git commit -m what?",
        "git commit -m 'unterminated",
    ):
        _refusal(command)


def test_a_plain_bare_word_message_is_accepted():
    assert _request("git commit -m hello")["message"] == "hello"
    assert _request("git commit -m fix-the-parser")["message"] == "fix-the-parser"


def test_a_commit_with_no_message_source_is_refused():
    # It would open an editor, which cannot work inside a hook.
    error = _refusal("git commit")
    assert "message" in error.lower()


# --------------------------------------------------------------------------
# The pinned flag set
# --------------------------------------------------------------------------
def test_the_two_carried_flags_are_the_codex_set():
    assert _request("git commit --signoff -m x")["options"] == ["--signoff"]
    assert _request("git commit --quiet -m x")["options"] == ["--quiet"]
    assert _request("git commit -m x")["options"] == []


@pytest.mark.parametrize("command", [
    "git commit --amend -m x",
    "git commit -F message.txt",
    "git commit --file=message.txt",
    "git commit -C HEAD",
    "git commit --squash HEAD -m x",
    "git commit --fixup HEAD",
    "git commit --author='A U Thor <a@u>' -m x",
    "git commit --date=2020-01-01 -m x",
    "git commit --no-verify -m x",
    "git commit --gpg-sign -m x",
    "git commit -am x",
    "git commit -m x -- file.py",
])
def test_every_other_form_is_refused_rather_than_quietly_committed(command):
    # An unsupported form must never be turned into an ordinary commit: that would publish
    # something other than what the agent asked for.
    _refusal(command)


def test_the_amend_refusal_says_why_rather_than_just_no():
    error = _refusal("git commit --amend -m x")
    assert "amend" in error.lower()
    assert "parent" in error.lower()
