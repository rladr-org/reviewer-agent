# SPDX-License-Identifier: MPL-2.0
"""Which Bash calls contain a commit-producing git invocation (#15).

Biased toward DETECTION: under the gate-owns-the-commit model a detected non-plain form is
refused with an accurate message (fail-closed), while a missed one commits unreviewed."""
import os

import pytest

from reviewer_codex import command_intent as rl

CWD = "/work/repo"


def _kind(command, cwd=CWD):
    return rl.commit_intent(command, cwd).kind


@pytest.mark.parametrize("command", [
    'echo "remember to git commit here"',
    "echo 'git commit -m x'",
    "printf '%s\\n' \"git commit\"",
    "cat > notes.md <<'EOF'\nrun git commit -m x afterwards\nEOF",
    "cat > notes.md <<EOF\ngit commit -m x\nEOF\necho done",
    "grep -n 'git commit' README.md",
    "# git commit -m x\nls",
    "git log --grep 'git commit'",
    "git committed",
    "gitk commit",
])
def test_text_that_only_mentions_a_commit_is_not_one(command):
    # Review Focus 2: data is not a command.
    assert _kind(command) is None


@pytest.mark.parametrize("command", [
    "git commit -m x",
    "cd sub && git commit -m x",
    "ls\ngit commit -m x",
    "git status; git commit -m x",
    "(git commit -m x)",
    "echo \"$(git commit -m x)\"",
    "echo `git commit -m x`",
    "FOO=1 git commit -m x",
    "git -C /work/repo commit -m x",
    "git --no-pager -C . commit -m x",
    "if true; then git commit -m x; fi",
])
def test_real_commit_positions_are_found(command):
    assert _kind(command) == "commit"


def test_a_literal_cd_moves_the_target():
    assert rl.commit_intent("cd /other/repo && git commit -m x", CWD).target == "/other/repo"


def test_a_relative_cd_is_joined_to_the_session_cwd():
    assert rl.commit_intent("cd sub && git commit -m x", CWD).target == os.path.join(CWD, "sub")


def test_an_expanded_cd_or_c_target_is_unknowable():
    assert rl.commit_intent('cd "$R" && git commit -m x', CWD).target is None
    assert rl.commit_intent('git -C "$R" commit -m x', CWD).target is None


@pytest.mark.parametrize("command", [
    "(cd /inactive); git commit -m x",       # shlex glues `);` into one token
    "(cd /inactive)\ngit commit -m x",
    "( cd /inactive ) && git commit -m x",
    "((cd /inactive)) && git commit -m x",
    "cd /inactive | true; git commit -m x",  # a pipeline element runs in a subshell
    "true | cd /inactive; git commit -m x",
    "cd /inactive & git commit -m x",        # so does a background job
    "env -C /inactive true; git commit -m x",  # a wrapper's directory is its command's only
    "x=$(\ncd /inactive\n); git commit -m x",  # a newline inside $( ) is not a top-level one
])
def test_a_cd_in_a_subshell_does_not_move_a_later_commit(command):
    # From an active repo, a cd the commit never sees must not retarget it to /inactive.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target == CWD, command


def test_a_cd_inside_the_subshell_moves_the_commit_inside_it():
    assert rl.commit_intent("(cd /other && git commit -m x)", CWD).target == "/other"


@pytest.mark.parametrize("command", [
    "cd /no/such/dir; git commit -m x",       # a failed cd leaves the shell where it was
    "cd /no/such/dir\ngit commit -m x",
    "cd /tmp || exit 1; git commit -m x",     # the commit's directory depends on cd's outcome
    "true ||\ncd /tmp; git commit -m x",      # `||` at a line end continues the chain: cd skipped
    "cd /tmp extra; git commit -m x",         # too many operands: cd fails and stays put
    "cd -X /tmp && git commit -m x",          # an unknown option: cd fails
    "if false; then cd /tmp; fi; git commit -m x",
    "for d in a; do cd /tmp; done; git commit -m x",
    "cd -P ~+ && git commit -m x",            # a directory-stack tilde expanduser cannot resolve
    "pushd /tmp; popd; git commit -m x",
])
def test_a_cd_whose_effect_is_uncertain_makes_the_target_unknowable(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


@pytest.mark.skipif(os.geteuid() == 0, reason="root can enter any directory")
def test_a_cd_into_an_unsearchable_directory_makes_the_target_unknowable(tmp_path):
    # isdir() is True, but `cd` fails without search permission and the shell stays put.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o600)
    try:
        intent = rl.commit_intent("cd {}; git commit -m x".format(locked), CWD)
        assert intent.kind == "commit" and intent.target is None
    finally:
        locked.chmod(0o700)


@pytest.mark.parametrize("command", [
    "HOME=/active cd; git commit -m x",       # the builtin reads the prefixed HOME
    "HOME=/active cd ~ && git commit -m x",
    "export HOME=/active; cd ~ && git commit -m x",
    'declare "HO""ME=/active"; cd && git commit -m x',
    "true; git -C ~/repo commit -m x",        # not the first command: HOME may have changed
])
def test_a_home_that_may_have_changed_is_not_trusted(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


@pytest.mark.parametrize("home", [None, ""])
def test_an_unset_or_empty_home_is_not_trusted(monkeypatch, home):
    # bash: a bare `cd` with HOME unset fails and stays put; Python would use the passwd home.
    if home is None:
        monkeypatch.delenv("HOME", raising=False)
    else:
        monkeypatch.setenv("HOME", home)
    for command in ("cd; git commit -m x", "cd ~ && git commit -m x"):
        intent = rl.commit_intent(command, CWD)
        assert intent.kind == "commit" and intent.target is None, command


def test_the_first_command_of_a_call_resolves_home(monkeypatch):
    monkeypatch.setenv("HOME", "/home/x")
    assert rl.commit_intent("git -C ~/repo commit -m x", CWD).target == "/home/x/repo"
    assert rl.commit_intent("cd ~/repo && git commit -m x", CWD).target == "/home/x/repo"


def test_a_cd_to_an_existing_directory_moves_a_later_commit(tmp_path):
    for sep in ("; ", "\n", " && ", " &&\n"):
        command = "cd {}{}git commit -m x".format(tmp_path, sep)
        assert rl.commit_intent(command, CWD).target == str(tmp_path), repr(sep)
    assert rl.commit_intent("cd -P -- {} && git commit -m x".format(tmp_path),
                            CWD).target == str(tmp_path)


@pytest.mark.parametrize("command", [
    "GIT_DIR=/other/.git git commit -m x",
    "GIT_WORK_TREE=/other git commit -m x",
    "git --git-dir=/other/.git commit -m x",
    "git --work-tree /other commit -m x",
])
def test_a_repository_named_outside_the_directory_is_unknowable(command):
    # The commit lands in a repository the cwd does not name; the gate must not gate cwd.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target is None, command


def test_a_substitution_runs_in_the_directory_of_its_own_command():
    assert rl.commit_intent('cd /tmp && echo "$(git commit -m x)"', CWD).target == "/tmp"
    assert rl.commit_intent("cd /tmp && cat <<EOF\n$(git commit -m x)\nEOF", CWD).target == "/tmp"


def test_a_heredoc_fed_to_a_shell_is_a_script():
    assert _kind("bash <<'EOF'\ngit commit -m x\nEOF") == "commit"
    assert _kind("bash <<< 'git commit -m x'") == "commit"  # a here-string too


@pytest.mark.parametrize("command", [
    "cat <<'EOF' | bash\ngit commit -m x\nEOF",
    'echo "git commit -m x" | sh',
    "printf '%s\\n' 'git commit -m x' | tee log | bash",
])
def test_a_script_piped_into_a_shell_is_judged(command):
    assert _kind(command) == "commit", command


@pytest.mark.parametrize("command", [
    "cat notes.md | grep 'git commit'",
    "echo 'git commit' | tee notes.txt",
])
def test_a_pipe_into_something_other_than_a_shell_is_data(command):
    assert _kind(command) is None, command


def test_an_unquoted_heredoc_runs_its_substitutions_and_a_quoted_one_does_not():
    assert _kind("cat <<EOF\n$(git commit -m x)\nEOF") == "commit"
    assert _kind("cat > s.sh <<'EOF'\n$(git commit -m x)\nEOF") is None


def test_an_unparseable_line_falls_back_to_the_old_regex():
    # Unterminated quote: shlex raises; the regex still sees a commit (fail-closed).
    assert _kind("git commit -m 'oops") == "commit"


@pytest.mark.parametrize("command", [
    "/usr/bin/git commit -m x",
    '"git" commit -m x',
    "'git' commit -m x",
    "\\git commit -m x",
    "env git commit -m x",
    "env -i PATH=/usr/bin git commit -m x",
    "command git commit -m x",
    "nohup git commit -m x",
    "timeout 30 git commit -m x",
    "echo x | xargs git commit -m",
    "bash -c 'git commit -m x'",
    "sh -c \"cd sub && git commit -m x\"",
    "bash -lc 'git commit -m x'",
    'eval "git commit -m x"',
    "bash <<'EOF'\ngit commit -m x\nEOF",
    "git -c alias.ci=commit ci -m x",
    "git -c 'alias.sh=!git commit -m x' sh",
])
def test_wrapped_and_aliased_commits_are_found(command):
    assert _kind(command) is not None, command


@pytest.mark.parametrize("command", [
    "python3 -c \"import subprocess; subprocess.run(['git','commit','-m','x'])\"",
    "node -e \"require('child_process').execSync('git commit -m x')\"",
    "python3 - <<'EOF'\nimport os; os.system('git commit -m x')\nEOF",
    "$GIT_BIN commit -m x",  # a command name the call never assigns
])
def test_opaque_commit_runners_are_refused(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "other" and intent.reason


def test_a_repository_alias_is_resolved(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "alias.ci", "commit"], check=True)
    assert rl.commit_intent("git ci -m x", str(tmp_path)).kind == "commit"


def test_a_wrapper_prefix_scopes_to_its_own_command():
    assert rl.commit_intent("env GIT_WORK_TREE=/other git commit -m x", CWD).target is None
    assert rl.commit_intent("env -C /other git commit -m x", CWD).target == "/other"
    assert rl.commit_intent("env -C /other true; git commit -m x", CWD).target == CWD


@pytest.mark.parametrize("command", [
    "env --chdir=/active git commit -m x",
    "env --chdir /active git commit -m x",
    "env -iC /active git commit -m x",
    "env -C/active git commit -m x",
    "sudo -D /active git commit -m x",
    "sudo --chdir=/active git commit -m x",
])
def test_every_spelling_of_a_wrapper_directory_option_moves_the_target(command):
    # From an inactive cwd, each of these commits in /active.
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "commit" and intent.target == "/active", command


def test_sudo_login_runs_from_a_home_directory_the_text_does_not_name():
    assert rl.commit_intent("sudo -i git commit -m x", CWD).target is None


@pytest.mark.parametrize("command", [
    "env -S 'git commit -m x'",
    "env --split-string='git commit -m x'",
    "env -iS 'git commit -m x'",
    "env -S 'GIT_DIR=/o/.git git commit -m x'",
])
def test_env_split_string_is_parsed_as_the_command_it_runs(command):
    # GNU env -S runs its value as a command line.
    assert _kind(command) == "commit", command


def test_an_eval_that_changes_directory_makes_the_target_unknowable():
    assert rl.commit_intent('eval "cd /other"; git commit -m x', CWD).target is None
    assert rl.commit_intent("cmd='cd /tmp'; eval \"$cmd\"; git commit -m x", CWD).target is None


def test_a_shell_reading_a_script_file_is_a_known_limit():
    # The gate cannot read what a script file contains.
    assert _kind("bash ./release.sh") is None


def test_a_cd_run_through_an_external_wrapper_does_not_move_the_shell():
    # `env` cannot run the shell builtin; the parent shell stays where it was.
    intent = rl.commit_intent("env -C /tmp cd /var; git commit -m x", CWD)
    assert intent.kind == "commit" and intent.target == CWD


@pytest.mark.parametrize("command", [
    "$GIT_BIN --no-pager -C /tmp commit -m x",   # a producer after global options
])
def test_code_the_text_cannot_show_is_refused(command):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "other" and intent.reason, command


# Task C2.2: an unresolved command word is refused only when the visible text names a
# commit-producing git subcommand. A commit held entirely in a variable or in command output
# set outside the call is a documented limit, like a script file.
@pytest.mark.parametrize("command", [
    '"$PYTHON" -m pytest -q',
    "$HOME/bin/tool --flag",
    'eval "$(ssh-agent -s)"',
    'eval "$UNSET_ELSEWHERE"',                    # a whole command the text does not show
    "$GIT_CMD -m x",                              # the environment may hold `git commit`
    'bash -c "$(curl -s https://example.com/x)"',  # code produced at run time
])
def test_an_unresolved_command_word_without_a_commit_producer_is_not_refused(command):
    assert _kind(command) is None, command


@pytest.mark.parametrize("command", [
    "cmd='git commit -m x'; eval \"$cmd\"",
    "cmd='git commit -m x'; bash -c \"$cmd\"",
    "export cmd='git commit -m x'; $cmd",
])
def test_a_command_assigned_earlier_in_the_call_is_resolved(command):
    assert _kind(command) == "commit", command


@pytest.mark.parametrize("command", [
    "cmd=git; false && cmd=echo; $cmd commit -m x",  # the second assignment may not run
    "cmd=git; (cmd=echo); $cmd commit -m x",          # a subshell's assignment does not persist
    "$GIT_BIN commit -m x",                           # a producer the text does show
    "git $SUBCOMMAND -m x",                           # git itself is visible
    # The expansion may be an interpreter or a shell running code the text does show.
    "\"$PYTHON\" -c 'import os; os.system(\"git commit -m x\")'",
    "\"$SHELL\" -c 'git commit -m x'",
    "\"$PYTHON\" - <<'EOF'\nimport os; os.system('git commit -m x')\nEOF",
])
def test_a_command_word_the_call_cannot_pin_down_is_refused(command):
    assert rl.commit_intent(command, CWD).kind == "other", command


def test_an_unresolved_config_value_may_define_an_alias():
    intent = rl.commit_intent('git -c "$ALIAS" ci -m x', CWD)
    assert intent.kind == "other", intent
    # Git applies the later value, so an earlier known alias proves nothing.
    intent = rl.commit_intent('git -c alias.ci=status -c "$ALIAS" ci -m x', CWD)
    assert intent.kind == "other", intent
    assert rl.commit_intent('git -c "$X" status', CWD).kind is None  # a builtin is not an alias


def test_an_unquoted_expansion_splits_into_git_arguments():
    intent = rl.commit_intent("opts='-C /tmp'; git $opts commit -m x", CWD)
    assert intent.kind == "commit" and intent.target == "/tmp"


def test_a_command_name_assigned_earlier_is_resolved():
    assert _kind("g=git; $g --no-pager -C /tmp commit -m x") == "commit"


def test_an_expansion_that_is_only_an_argument_is_not_code():
    assert _kind("bash -c 'echo $HOME'") is None


def test_inline_argv_with_global_options_is_refused():
    command = ("python3 -c \"import subprocess; "
               "subprocess.run(['git','-C','/tmp','commit','-m','x'])\"")
    assert rl.commit_intent(command, CWD).kind == "other"


def test_an_alias_in_a_repository_the_gate_cannot_name_is_refused():
    intent = rl.commit_intent("GIT_DIR=/repo/.git git ci -m x", CWD)
    assert intent.kind == "other" and intent.target is None


@pytest.mark.parametrize("command,needle", [
    ("git commit-tree abc -m x", "commit-tree"),
    ("git merge feature", "--no-commit"),
    ("git merge --no-ff feature", "--no-commit"),
    ("git merge --continue", "--no-commit"),
    ("git cherry-pick abc123", "cherry-pick -n"),
    ("git revert HEAD", "--no-commit"),
    ("git am 0001.patch", "git apply --index"),
    ("git pull", "--ff-only"),
    ("git pull --rebase", "--ff-only"),
    # Git resolves these options last-wins, so a later switch re-enables the commit:
    ("git merge --no-commit --commit feature", "--no-commit"),
    ("git merge --no-commit --comm feature", "--no-commit"),  # an abbreviation of --commit
    ("git merge --squash --no-squash feature", "--no-commit"),
    ("git merge --ff-only --no-ff feature", "--no-commit"),
    ("git merge --ff-only --ff feature", "--no-commit"),
    ("git cherry-pick -n --commit abc123", "cherry-pick -n"),  # --commit negates --no-commit
    ("git revert --no-commit --commit HEAD", "--no-commit"),
    ("git cherry-pick --skip", "cherry-pick -n"),  # resumes the sequence, committing the rest
])
def test_other_commit_producers_are_refused_with_the_no_commit_route(command, needle):
    intent = rl.commit_intent(command, CWD)
    assert intent.kind == "other" and needle in intent.reason, command


@pytest.mark.parametrize("command", [
    "git merge --no-commit --no-ff feature", "git merge --squash feature",
    "git merge --ff-only origin/main", "git merge --abort",
    "git merge --commit --no-commit feature", "git merge --no-commit --ff feature",
    "git merge --squash --commit feature",  # git refuses the combination; nothing is committed
    "git cherry-pick -n abc123", "git cherry-pick --no-commit abc", "git cherry-pick -xn abc",
    "git revert --no-commit HEAD", "git revert -n HEAD", "git am --abort",
    "git merge-base a b", "git log --merges",
])
def test_no_commit_modes_are_allowed(command):
    assert rl.commit_intent(command, CWD).kind is None, command


@pytest.fixture
def pull_repo(git_repo, tmp_path, monkeypatch):
    """A repository on branch `main` whose pull mode comes only from its own config."""
    import subprocess
    for name in ("HOME", "XDG_CONFIG_HOME"):
        monkeypatch.setenv(name, str(tmp_path))  # no global pull.rebase from the host
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    subprocess.run(["git", "-C", str(git_repo), "branch", "-m", "main"], check=True)
    return str(git_repo)


@pytest.mark.parametrize("command", [
    "git pull --ff-only", "git pull --no-commit", "git pull --squash",
    "git pull --rebase --no-rebase --no-commit", "git pull --rebase=false --squash",
])
def test_no_commit_pull_modes_are_allowed(pull_repo, command):
    assert rl.commit_intent(command, pull_repo).kind is None, command


@pytest.mark.parametrize("command", [
    "git pull --no-commit --commit", "git pull --squash --no-squash", "git pull --ff-only --ff",
    "git pull --no-commit --rebase", "git pull -r --no-commit", "git pull --ff-only --rebase",
    "git -c pull.rebase=true pull --no-commit",
    "git -c branch.main.rebase=merges pull --squash",
    'git -c "$CFG" pull --no-commit',  # may set pull.rebase
])
def test_pull_that_rebases_or_re_enables_the_commit_is_refused(pull_repo, command):
    intent = rl.commit_intent(command, pull_repo)
    assert intent.kind == "other" and "--ff-only" in intent.reason, command


@pytest.mark.parametrize("command", [
    "git merge -m --no-commit feature",        # the token is -m's message, not a switch
    "git merge --message --squash feature",
    "git merge -s --ff-only feature",
    "git cherry-pick -m --no-commit abc",
    "git cherry-pick -Sn HEAD",           # `n` is -S's attached key id, not --no-commit
    "git revert -Sn HEAD",
    "git merge --some-new-option --no-commit feature",  # it may have taken the switch as a value
])
def test_a_no_commit_switch_used_as_an_option_value_does_not_count(command):
    assert rl.commit_intent(command, CWD).kind == "other", command


@pytest.mark.parametrize("command", [
    "git merge -m msg --no-commit feature",
    "git merge --strategy ours --no-commit feature",
    "git merge -X theirs --squash feature",
    "git merge --no-edit --no-commit feature",
    "git merge -S --no-commit feature",   # -S takes its key only attached
    "git cherry-pick -nS HEAD",
])
def test_option_values_are_skipped_before_the_no_commit_switch(command):
    assert rl.commit_intent(command, CWD).kind is None, command


def test_ff_only_overrides_a_configured_rebase_on_git_that_honours_it(pull_repo):
    # git >= 2.34 fast-forwards or fails under --ff-only whatever pull.rebase says; older git
    # (Ubuntu 20.04's 2.25) rebases, so there it stays refused.
    import subprocess
    subprocess.run(["git", "-C", pull_repo, "config", "pull.rebase", "true"], check=True)
    version = subprocess.run(["git", "--version"], capture_output=True, text=True).stdout
    numbers = tuple(int(n) for n in version.split()[2].split(".")[:2])
    expected = None if numbers >= (2, 34) else "other"
    assert rl.commit_intent("git pull --ff-only", pull_repo).kind == expected


@pytest.mark.parametrize("key", ["pull.rebase", "branch.main.rebase"])
def test_configured_rebase_makes_a_no_commit_pull_commit(pull_repo, key):
    # Verified: under pull.rebase=true, `git pull --no-commit` rebases and moves HEAD.
    import subprocess
    subprocess.run(["git", "-C", pull_repo, "config", key, "true"], check=True)
    assert rl.commit_intent("git pull --no-commit", pull_repo).kind == "other"
    assert rl.commit_intent("git pull --no-rebase --no-commit", pull_repo).kind is None
