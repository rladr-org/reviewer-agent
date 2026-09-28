# SPDX-License-Identifier: MPL-2.0
import pytest

from reviewer_git import commit_intent


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
