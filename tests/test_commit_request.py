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

pytestmark = pytest.mark.smoke  # install-time subset (tests/test_smoke_subset.py)


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


def test_staging_in_the_same_call_gets_an_accurate_refusal():
    error = _refusal("git add f.py" + " && " + "git commit -m x")
    assert "stages and commits in one call" in error
    assert "git add f.py" in error
    assert "(here: git commit" not in error  # the old text named the COMMIT as the dropped part


def test_the_refusal_names_the_parts_that_would_be_dropped():
    error = _refusal("git commit -m x" + " && " + "git push")
    assert "git push" in error and "git commit -m x" not in error.split("(here:")[-1]


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
    assert _request('git commit -m "two words"')["message"] == "two words"


def test_an_escape_the_lexer_and_a_shell_resolve_differently_is_refused():
    # A shell turns `\$` inside double quotes into a literal `$`; Python's lexer keeps the
    # backslash. Committing either one would be committing text that differs from what was
    # written, so the gate refuses and points at single quotes, which both agree on.
    error = _refusal('git commit -m "cost \\$5"')
    assert "backslash" in error.lower()
    assert _request("git commit -m 'cost \\$5'")["message"] == "cost \\$5"


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


# --------------------------------------------------------------------------
# Quoting belongs to the lexer, not to hand-rolled boundary logic
# --------------------------------------------------------------------------
def test_a_multiline_message_survives():
    # A newline INSIDE a quoted message is message text, not a command boundary. Deciding that
    # from line numbers rather than from quote state split the message in half.
    assert _request('git commit -m "first\nsecond"')["message"] == "first\nsecond"


def test_escaped_double_quotes_inside_a_message_survive():
    assert _request('git commit -m "say \\"hi\\""')["message"] == 'say "hi"'


def test_a_message_may_be_the_word_git():
    # Counting `git` tokens to find extra commands refused a perfectly ordinary message.
    assert _request("git commit -m git")["message"] == "git"
    assert _request("git commit -m 'git push is not run here'")["message"] == \
        "git push is not run here"


def test_operators_quoted_inside_a_message_are_message_text():
    assert _request("git commit -m 'a && b'")["message"] == "a && b"
    assert _request("git commit -m 'a; b'")["message"] == "a; b"
    assert _request("git commit -m 'pipe | redirect > file'")["message"] == \
        "pipe | redirect > file"


def test_a_second_command_after_a_newline_is_still_refused():
    # The structural guarantee must survive dropping the newline sentinel: the trailing command's
    # words become arguments the parser does not recognise, and unrecognised means refused.
    error = _refusal("git commit -m x\ngit push")
    assert "only" in error.lower() or "one" in error.lower()


# --------------------------------------------------------------------------
# A newline outside quotes separates commands; inside quotes it is message text
# --------------------------------------------------------------------------
def test_a_newline_outside_quotes_separates_commands():
    # `git commit -m x` on one line and `-m y` on the next are two commands. Folding the
    # newline into whitespace made the second line's words look like more arguments, so the
    # gate would have committed a message, or a flag, belonging to a command it never ran.
    for command in (
        "git commit -m x\n-m y",
        "git commit -m x\n--quiet",
        "git commit -m x\ngit push",
    ):
        error = _refusal(command)
        assert "one" in error.lower() or "only" in error.lower() or "line" in error.lower(), \
            command


def test_a_trailing_newline_is_not_a_second_command():
    assert _request("git commit -m hello\n")["message"] == "hello"
    assert _request("git commit -m hello\n  \n")["message"] == "hello"


def test_a_backslash_line_continuation_is_one_command():
    assert _request("git commit \\\n  -m hello")["message"] == "hello"


# --------------------------------------------------------------------------
# The message rule is for the message, not for the repository target
# --------------------------------------------------------------------------
def test_a_tilde_in_the_repository_target_is_still_accepted():
    # `git_c_effective_dir` resolves `~` with expanduser, so refusing it here would drop
    # support the gate already has. The strict unquoted-character rule exists to protect the
    # MESSAGE, and applies from the subcommand onwards.
    assert _request("git -C ~/repo commit -m hello")["message"] == "hello"
    assert _request("git -C /abs/path commit -m hello")["message"] == "hello"


def test_a_dynamic_repository_target_is_still_refused():
    # Fail-closed handling of a target the gate cannot resolve must not be weakened by the
    # narrower message rule.
    _refusal('git -C "$REPO" commit -m hello')
    _refusal("git -C `pwd`/repo commit -m hello")


def test_a_shell_expanded_repository_target_is_refused():
    # The shell would expand the glob to a real path while `git_c_effective_dir` resolves the
    # literal text, so the gate would audit one repository while git commits in another. Only
    # `~` is exempt, because expanduser resolves it the same way the shell does.
    for command in (
        "git -C /tmp/repo* commit -m x",
        "git -C /tmp/repo{a,b} commit -m x",
        "git -C /tmp/repo? commit -m x",
        "git -C /tmp/[abc]repo commit -m x",
    ):
        error = _refusal(command)
        assert "quote" in error.lower() or "shell" in error.lower(), command


def test_the_dash_c_equals_form_is_refused():
    # git itself rejects `-C=path` (it wants `-C <path>`), and git_c_effective_dir ignores the
    # `=` form and resolves to the session cwd -- so accepting it would let the gate commit in a
    # different directory from the one the command names.
    error = _refusal("git -C=/tmp/other commit -m hello")
    assert "-C" in error


def test_a_literal_repository_target_is_still_accepted():
    assert _request("git -C /tmp/repo commit -m hello")["message"] == "hello"
    assert _request("git -C ~/repo commit -m hello")["message"] == "hello"
    # A quoted target reaches git as literal text; resolving it is git_c_effective_dir's job,
    # not the parser's, so the parser accepts it rather than second-guessing that resolution.
    assert _request("git -C '/tmp/my repo' commit -m hello")["message"] == "hello"


def test_only_the_tilde_forms_expanduser_actually_matches_are_exempt():
    # `expanduser` leaves `~+`, `~-` and `~1` untouched, while a shell expands them from the
    # directory stack. The gate would resolve the literal text and audit a different repository
    # from the one git targets, which is the same fail-open as an unresolvable `-C`.
    for command in (
        "git -C ~+ commit -m x",
        "git -C ~- commit -m x",
        "git -C ~1 commit -m x",
        "git -C ~+/sub commit -m x",
    ):
        _refusal(command)


def test_the_plain_tilde_forms_remain_accepted():
    assert _request("git -C ~ commit -m hello")["message"] == "hello"
    assert _request("git -C ~/repo commit -m hello")["message"] == "hello"
    assert _request("git -C ~/repo/deeper commit -m hello")["message"] == "hello"


def test_named_user_and_embedded_tildes_in_a_target_are_accepted():
    # A shell expands a tilde-prefix only at the START of a word, and resolves `~name` through
    # the user database exactly as expanduser does. Only the directory-stack prefixes diverge,
    # so those are the only ones worth refusing.
    assert _request("git -C ~root/repo commit -m hello")["message"] == "hello"
    assert _request("git -C ~nobody commit -m hello")["message"] == "hello"
    assert _request("git -C /tmp/a~b commit -m hello")["message"] == "hello"
    assert _request("git -C /tmp/repo~ commit -m hello")["message"] == "hello"


def test_only_directory_stack_prefixes_are_refused_in_a_target():
    for command in (
        "git -C ~+ commit -m x",
        "git -C ~- commit -m x",
        "git -C ~1 commit -m x",
        "git -C ~+2/sub commit -m x",
        "git -C ~-3 commit -m x",
    ):
        _refusal(command)


def test_a_tilde_inside_a_message_is_literal_unless_it_leads():
    # Not expanded mid-word, so it is ordinary message text.
    assert _request("git commit -m a~b")["message"] == "a~b"
    # Leading, it IS expanded, and the committed message would not be the text written.
    _refusal("git commit -m ~home")


def test_a_continuation_cannot_hide_a_directory_stack_prefix():
    # A shell removes the continuation BEFORE expanding, so `~+\<newline>/sub` is the word
    # `~+/sub` and expands from the directory stack. Classifying the prefix on the raw text saw
    # `+\` instead and let it through.
    _refusal("git -C ~+\\\n/sub commit -m x")
    _refusal("git -C ~\\\n+ commit -m x")


def test_a_continuation_inside_an_ordinary_target_is_still_fine():
    assert _request("git -C /tmp/re\\\npo commit -m hello")["message"] == "hello"


def test_an_apostrophe_inside_double_quotes_does_not_open_a_quote_span():
    # Inside double quotes an apostrophe is ordinary text, so the continuation after it is
    # still removed by the shell. Treating it as an opening single quote left the continuation
    # in place and would have committed a different message from the one asked for.
    command = 'git commit -m "a\'' + "\\\n" + 'b"'
    assert _request(command)["message"] == "a'b"


def test_an_apostrophe_inside_double_quotes_is_kept_in_the_message():
    assert _request('git commit -m "it\'s fine"')["message"] == "it's fine"


def test_a_continuation_inside_single_quotes_is_literal_text():
    # Single quotes protect everything, including a backslash-newline: the shell does NOT
    # remove it there, so it stays part of the message.
    command = "git commit -m 'a" + "\\\n" + "b'"
    assert _request(command)["message"] == "a\\\nb"
