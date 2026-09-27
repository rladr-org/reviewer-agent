# SPDX-License-Identifier: MPL-2.0
"""Keep the Claude parser aligned with defects found reviewing the Codex copy."""
import pytest

import reviewer_lib as rl


@pytest.mark.parametrize("command", [
    "function f { git -C /protected commit -m x; }; f",
    'function f { "/usr/bin/git" -C /protected commit -m x; }; f',
    "bash -c 'function f { command git -C /protected commit -m x; }; f'",
])
def test_inline_function_commit_is_detected(command):
    intent = rl.commit_intent(command, "/work/repo")
    assert intent.kind == "commit"
    assert intent.target == "/protected"


@pytest.mark.parametrize("delimiter", [r"\EOF", r"E\OF", 'E"OF"', "E'OF'"])
def test_partly_quoted_heredoc_is_data(delimiter):
    script = "cat <<" + delimiter + "\ngit commit -m x\n$(git commit -m y)\nEOF\n"
    assert rl.commit_intent(script, "/work/repo").kind is None


@pytest.mark.parametrize("delimiter", [r"\EOF", r"E\OF", 'E"OF"', "E'OF'"])
def test_partly_quoted_heredoc_to_shell_is_code(delimiter):
    script = "bash <<" + delimiter + "\ngit commit -m x\nEOF\n"
    assert rl.commit_intent(script, "/work/repo").kind == "commit"


def test_function_with_only_commit_text_is_inert():
    assert rl.commit_intent("function f { printf '%s' 'git commit'; }; f", "/work/repo").kind is None


@pytest.mark.parametrize("script", [
    "/usr/bin/git -C /protected commit -m x",
    '"/usr/bin/git" -C /protected commit -m x',
    "/usr/bin/git -C /protected commit-tree HEAD^{tree} -m x",
    "/usr/bin/git -C /protected merge topic",
])
def test_pipeline_script_absolute_git_is_detected(script):
    import shlex
    command = "printf '%s\\n' " + shlex.quote(script) + " | bash"
    assert rl.commit_intent(command, "/work/repo").kind is not None
    assert rl.commit_intent(command.replace(" | bash", " | cat"), "/work/repo").kind is None
