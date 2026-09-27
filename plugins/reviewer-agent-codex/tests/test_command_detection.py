# SPDX-License-Identifier: MPL-2.0
"""Gate real executable forms, without turning quoted documentation into commits."""
import pytest

from reviewer_codex import gate, git, state


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(state, "enabled_feature_roots", lambda feature: ["/protected"])
    monkeypatch.setattr(state, "feature_enabled", lambda path, feature: path == "/protected")
    monkeypatch.setattr(state, "project_root", lambda path: path)


def check(command):
    return gate.review_commit({"cwd": "/host", "session_id": "test-session",
                               "tool_input": {"command": command}})


@pytest.mark.parametrize("command", [
    'echo "remember to git commit after review"',
    "printf '%s' 'git -C /protected commit -m example'",
    "cat <<'EOF'\ngit commit -m example\nEOF",
    "python3 -c \"print('run git commit after review')\"",
    "python3 - <<'EOF'\nopen('report.md', 'w').write('Next: `git commit -m x`')\nEOF",
    "node -e \"console.log('git commit')\"",
    '"$PYTHON" -m pytest -q',
    'eval "$(ssh-agent -s)"',
])
def test_harmless_text_and_unrelated_expansions_are_not_commits(enabled, command):
    assert check(command) is None
    assert not git.is_git_commit(command)


@pytest.mark.parametrize("command", [
    '/usr/bin/git -C /protected commit -m test',
    '"/usr/bin/git" -C /protected commit -m test',
    "bash -c '/usr/bin/git -C /protected commit -m test'",
    'GIT_BIN=git; "$GIT_BIN" -C /protected commit -m test',
    '$GIT_BIN -C /protected commit -m test',
    "python3 -c \"import subprocess; subprocess.run(['git', '-C', '/protected', 'commit'])\"",
    "git -C /protected commit-tree HEAD^{tree} -m test",
    "git -C /protected merge topic",
    "git -C /protected cherry-pick HEAD~1",
    "git -C /protected revert HEAD",
    "git -C /protected am change.patch",
    "git -C /protected pull",
])
def test_unsupported_executable_commit_forms_are_denied_without_a_ticket(enabled, command):
    result = check(command)
    assert result is not None, command
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review_token=" not in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("command", [
    "git -C /inactive commit -m one; git -C /protected commit -m two",
    "bash -c 'git -C /inactive commit -m one; git -C /protected commit -m two'",
    'echo "$(git -C /inactive commit -m one; git -C /protected commit -m two)"',
])
def test_disabled_first_target_does_not_hide_a_later_protected_commit(enabled, command):
    result = check(command)
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review_token=" not in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_inactive_explicit_target_stays_inert(enabled):
    assert check("git -C /inactive commit -m test") is None


@pytest.mark.parametrize("producer", ["merge topic", "cherry-pick HEAD~1", "revert HEAD", "am change.patch", "commit-tree HEAD^{tree} -m test"])
def test_known_target_producer_explains_the_unsupported_operation(enabled, producer):
    result = check("git -C /protected " + producer)
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Unsupported commit-producing form" in reason
    assert "explicit absolute repository target" not in reason


@pytest.mark.parametrize("command", [
    "function f { git -C /protected commit -m x; }; f",
    'function f { "/usr/bin/git" -C /protected commit -m x; }; f',
    "bash -c 'function f { command git -C /protected commit -m x; }; f'",
])
def test_inline_bash_function_cannot_hide_a_protected_commit(enabled, command):
    result = check(command)
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review_token=" not in result["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("delimiter", [r"\EOF", r"E\OF", 'E"OF"', "E'OF'"])
def test_quoted_heredoc_word_keeps_commit_text_and_substitutions_inert(enabled, delimiter):
    command = "cat <<" + delimiter + "\ngit -C /protected commit -m x\n$(git -C /protected commit -m y)\nEOF\n"
    assert check(command) is None
    assert not git.is_git_commit(command)


@pytest.mark.parametrize("delimiter", [r"\EOF", r"E\OF", 'E"OF"', "E'OF'"])
def test_quoted_heredoc_fed_to_shell_still_checks_its_script(enabled, delimiter):
    command = "bash <<" + delimiter + "\ngit -C /protected commit -m x\nEOF\n"
    result = check(command)
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_inert_function_body_does_not_become_a_commit(enabled):
    assert check("function f { printf '%s' 'git commit'; }; f") is None


@pytest.mark.parametrize("script", [
    "/usr/bin/git -C /protected commit -m x",
    '"/usr/bin/git" -C /protected commit -m x',
    "/usr/bin/git -C /protected commit-tree HEAD^{tree} -m x",
    "/usr/bin/git -C /protected merge topic",
])
def test_pipeline_script_cannot_hide_absolute_git(enabled, script):
    import shlex
    command = "printf '%s\\n' " + shlex.quote(script) + " | bash"
    result = check(command)
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "review_token=" not in result["hookSpecificOutput"]["permissionDecisionReason"]
    assert check(command.replace(" | bash", " | cat")) is None


@pytest.mark.parametrize("command", [
    '''"$PYTHON" -c 'print("git commit")' ''',
    '''perl -e 'open(my $f, ">", "report.txt"); print $f "git commit";' ''',
])
def test_interpreter_report_text_does_not_imply_spawning(enabled, command):
    assert check(command) is None
    assert not git.is_git_commit(command)


@pytest.mark.parametrize("command", [
    '''"$PYTHON" -c 'import subprocess; subprocess.run(["git", "commit"])' ''',
    '''"$SHELL" -c 'git -C /protected commit -m x' ''',
    '''perl -e 'open(my $f, "|-", "git", "commit");' ''',
])
def test_interpreter_spawn_forms_still_require_review(enabled, command):
    result = check(command)
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
