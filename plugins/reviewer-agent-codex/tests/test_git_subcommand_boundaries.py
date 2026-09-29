# SPDX-License-Identifier: MPL-2.0
import shlex

import pytest

from reviewer_codex.command_intent import commit_intent


@pytest.mark.parametrize("subcommand", ["merge-base HEAD main", "commit-graph verify"])
@pytest.mark.parametrize("template", [
    'python3 -c "import subprocess; subprocess.run(\'git {subcommand}\', shell=True)"',
    "printf '%s\\n' 'git {subcommand}' | sh",
])
def test_hyphenated_read_only_git_commands_are_not_commit_producers(subcommand, template):
    assert commit_intent(template.format(subcommand=subcommand), "/work/repo").kind is None


@pytest.mark.parametrize("subcommand", ["commit", "commit-tree", "merge", "cherry-pick", "revert", "am", "pull"])
def test_inline_git_producers_remain_detected(subcommand):
    command = 'python3 -c "import subprocess; subprocess.run(\'git {}\', shell=True)"'.format(subcommand)
    assert commit_intent(command, "/work/repo").kind is not None


# `am` and `pull` are ordinary words, so they count only as the subcommand itself: after `git` and
# its global options. The unambiguous producers keep the loose match that catches assembled text.
@pytest.mark.parametrize("script", [
    "import subprocess; subprocess.run(['git', 'status']); print('I am done')",
    "import subprocess; subprocess.run(['git', 'status']); print('pull request is ready')",
    "import os; os.system('git log -1'); print('am/pm')",
])
def test_am_and_pull_as_plain_words_are_not_commit_producers(script):
    command = "python3 -c " + shlex.quote(script)
    assert commit_intent(command, "/work/repo").kind is None
    assert commit_intent("printf '%s\\n' " + shlex.quote(script) + " | sh", "/work/repo").kind is None


@pytest.mark.parametrize("script", [
    "import subprocess; subprocess.run(['git', '-C', '/x', 'pull'])",
    "import subprocess; subprocess.run(['git', 'pull', '--rebase'])",
    "import subprocess; subprocess.run('git --no-pager am fix.patch', shell=True)",
    "import subprocess; subprocess.run('git -c user.name=x am fix.patch', shell=True)",
    "import subprocess; subprocess.run('/usr/bin/git --git-dir=/x/.git pull', shell=True)",
    "import subprocess; subprocess.run(['git', 'status']); subprocess.run(['git', 'am', 'p'])",
])
def test_am_and_pull_as_git_subcommands_remain_detected(script):
    assert commit_intent("python3 -c " + shlex.quote(script), "/work/repo").kind is not None
