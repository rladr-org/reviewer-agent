# SPDX-License-Identifier: MPL-2.0
"""Uninstalling removes this plugin's configuration and nothing else.

Removal is where a plugin does its most dangerous writing: everything it touches is shared with
other tools. The marketplace file lists other people's plugins, the Codex config holds other
namespaces, `~/plugins/` holds other checkouts. A tidy-up that takes one neighbour with it is
worse than leaving state behind, because the user finds out later and cannot tell what happened.

So every test here installs a NEIGHBOUR alongside the plugin and asserts it survived. The other
property is idempotence: running the uninstaller twice, or on a machine where installation never
finished, must succeed quietly -- a removal path that only works once is a removal path people
stop trusting halfway through a failed install.
"""
import json
import os
from pathlib import Path

import pytest

from reviewer_codex import install as install_module
from reviewer_codex import uninstall as uninstall_module


PLUGIN = "reviewer-agent-codex"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "checkout"
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": PLUGIN, "version": "1.2.0-beta.1+codex.1"}) + "\n",
        encoding="utf-8",
    )
    return root


def marketplace_path(home):
    return home / ".agents" / "plugins" / "marketplace.json"


def install(source, home, neighbour=True):
    """Install the plugin, and put an unrelated plugin next to it in every shared location."""
    install_module.install_local(source, home=home, install=False)
    if neighbour:
        path = marketplace_path(home)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["plugins"].append({"name": "someone-elses-plugin", "source": {"source": "local"}})
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        other = home / "plugins" / "someone-elses-plugin"
        other.mkdir(parents=True, exist_ok=True)
    cache = home / ".codex" / "plugins" / "cache" / "personal" / PLUGIN / "1.2.0-beta.1+codex.1"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "marker").write_text("cached\n", encoding="utf-8")
    return cache


def test_uninstall_removes_the_marketplace_entry_and_leaves_the_neighbour(source, home):
    install(source, home)

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    entries = json.loads(marketplace_path(home).read_text(encoding="utf-8"))["plugins"]
    assert [entry["name"] for entry in entries] == ["someone-elses-plugin"]
    assert "marketplace entry" in " ".join(result.removed)


def test_uninstall_removes_only_its_own_source_link(source, home):
    install(source, home)

    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert not (home / "plugins" / PLUGIN).exists()
    assert (home / "plugins" / "someone-elses-plugin").is_dir()


def test_uninstall_leaves_a_source_link_that_points_somewhere_else(source, home, tmp_path):
    install(source, home)
    link = home / "plugins" / PLUGIN
    link.unlink()
    unrelated = tmp_path / "unrelated-checkout"
    unrelated.mkdir()
    link.symlink_to(unrelated, target_is_directory=True)

    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    # Someone else's link under our name is still someone else's; removing it would delete a
    # path this plugin never created.
    assert link.is_symlink()
    assert link.resolve() == unrelated.resolve()


def test_uninstall_asks_codex_to_remove_the_installed_plugin(source, home):
    install(source, home)
    commands = []

    uninstall_module.uninstall_local(source, home=home, run_command=commands.append)

    assert ["codex", "plugin", "remove", PLUGIN + "@personal"] in commands


def test_a_failing_codex_removal_does_not_stop_the_rest_of_the_cleanup(source, home):
    install(source, home)

    def refuse(command):
        raise OSError("codex is not installed here")

    result = uninstall_module.uninstall_local(source, home=home, run_command=refuse)

    # The CLI may be gone, or the plugin may already be uninstalled. Neither is a reason to
    # leave the marketplace entry and the symlink behind.
    assert not (home / "plugins" / PLUGIN).exists()
    assert json.loads(marketplace_path(home).read_text(encoding="utf-8"))["plugins"] == [
        {"name": "someone-elses-plugin", "source": {"source": "local"}}
    ]
    assert any("could not" in note.lower() for note in result.warnings), result.warnings


def test_uninstall_removes_only_its_own_config_namespace(source, home):
    install(source, home)
    namespaces = ["someone-elses-namespace", uninstall_module.DIRECT_TOOL_NAMESPACE]

    remaining = uninstall_module.remove_direct_namespace(
        namespaces, uninstall_module.DIRECT_TOOL_NAMESPACE
    )

    assert remaining == ["someone-elses-namespace"]


def test_uninstall_is_idempotent(source, home):
    install(source, home)

    first = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)
    second = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert first.removed
    assert second.removed == []
    assert second.warnings == [] or all("already" in note.lower() for note in second.warnings)


def test_uninstalling_a_plugin_that_was_never_installed_succeeds_quietly(source, home):
    home.mkdir(parents=True)

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert result.removed == []


def test_state_is_kept_unless_purging_is_requested(source, home, monkeypatch, tmp_path):
    state = tmp_path / "state"
    (state / "reviews").mkdir(parents=True)
    (state / "reviews" / "verdict.txt").write_text("APPROVED\n", encoding="utf-8")
    monkeypatch.setenv("REVIEWER_CODEX_STATE_DIR", str(state))
    install(source, home)

    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)
    assert (state / "reviews" / "verdict.txt").exists(), "verdicts are not the installer's to delete"

    uninstall_module.uninstall_local(
        source, home=home, run_command=lambda cmd: None, purge_state=True
    )
    assert not state.exists()


def test_the_cache_directory_is_removed_only_for_this_plugin(source, home):
    cache = install(source, home)
    neighbour_cache = cache.parent.parent / "someone-elses-plugin" / "1.0.0"
    neighbour_cache.mkdir(parents=True)

    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert not cache.parent.exists()
    assert neighbour_cache.is_dir()


def test_the_cli_reports_what_it_removed_and_what_it_could_not(tmp_path, monkeypatch):
    # Driving the script, not the function: the script is what a person runs, and the manual
    # config step is the one thing the automated path deliberately does not do -- so it has to
    # be printed, every time, rather than left in a README nobody reads at uninstall time.
    import subprocess
    import sys

    script = ROOT / "scripts" / "uninstall_local.py"
    environment = dict(os.environ, HOME=str(tmp_path), CODEX_HOME=str(tmp_path / ".codex"),
                       REVIEWER_CODEX_STATE_DIR=str(tmp_path / "state"))

    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, env=environment
    )

    assert result.returncode == 0, result.stderr
    assert "Nothing to remove" in result.stdout
    assert "direct_only_tool_namespaces" in result.stdout
    assert "mcp__reviewer_agent" in result.stdout
    assert "--purge-state" in result.stdout


def test_a_marketplace_name_that_is_a_path_cannot_redirect_the_delete(source, home, tmp_path):
    # `name` is read from JSON and joined into a cache path that gets rmtree'd. An absolute
    # value replaces the whole path when joined; "../.." walks out of the cache root. Neither
    # may reach the filesystem.
    install(source, home)
    victim = tmp_path / "not-the-cache"
    victim.mkdir()
    (victim / "important.txt").write_text("keep me\n", encoding="utf-8")
    path = marketplace_path(home)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = str(victim)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert (victim / "important.txt").exists()
    assert any("marketplace name" in note for note in result.warnings), result.warnings


def test_a_traversing_marketplace_name_is_refused(source, home):
    install(source, home)
    path = marketplace_path(home)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["name"] = "../../../../tmp"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert any("marketplace name" in note for note in result.warnings), result.warnings


def test_a_marketplace_file_that_is_not_an_object_does_not_crash_cleanup(source, home):
    install(source, home)
    marketplace_path(home).write_text("[]\n", encoding="utf-8")

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    # The rest of the cleanup still happened.
    assert not (home / "plugins" / PLUGIN).exists()
    assert any("JSON object" in note for note in result.warnings), result.warnings


def test_an_unreadable_marketplace_file_does_not_crash_cleanup(source, home):
    install(source, home)
    marketplace_path(home).write_text("{not json\n", encoding="utf-8")

    result = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert not (home / "plugins" / PLUGIN).exists()
    assert any("could not read" in note for note in result.warnings), result.warnings


def test_a_plugin_manifest_name_that_is_a_path_is_refused(tmp_path, home):
    bad = tmp_path / "bad-checkout"
    (bad / ".codex-plugin").mkdir(parents=True)
    (bad / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "../../escape"}) + "\n", encoding="utf-8"
    )

    with pytest.raises(uninstall_module.InstallNameError):
        uninstall_module.uninstall_local(bad, home=home, run_command=lambda cmd: None)


# --------------------------------------------------------------------------
# repeated lifecycles, from a clean profile and from a pre-configured one
# --------------------------------------------------------------------------
def test_install_uninstall_reinstall_returns_to_the_same_state(source, home):
    """The cycle a user actually performs, twice, with the state compared.

    Install-then-uninstall-then-install is where half-cleaned state shows up: a stale
    marketplace entry duplicates, a symlink is left pointing at an old checkout, a cache
    directory shadows the new version. Comparing the two installed states catches all three
    without having to predict which one breaks.
    """
    install_module.install_local(source, home=home, install=False)
    first = json.loads(marketplace_path(home).read_text(encoding="utf-8"))
    first_link = (home / "plugins" / PLUGIN).resolve()

    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)
    install_module.install_local(source, home=home, install=False)

    second = json.loads(marketplace_path(home).read_text(encoding="utf-8"))
    assert second == first
    assert (home / "plugins" / PLUGIN).resolve() == first_link
    assert [entry["name"] for entry in second["plugins"]].count(PLUGIN) == 1


def test_installing_over_an_existing_installation_does_not_duplicate_anything(source, home):
    # The update path: install again without uninstalling first, which is what `git pull` plus
    # a reinstall does.
    install_module.install_local(source, home=home, install=False)
    install_module.install_local(source, home=home, install=False)

    entries = json.loads(marketplace_path(home).read_text(encoding="utf-8"))["plugins"]
    assert [entry["name"] for entry in entries].count(PLUGIN) == 1


def test_a_preconfigured_profile_keeps_its_own_marketplace_shape(source, home):
    """A profile that already has a marketplace, plugins and settings must come back unchanged.

    "Clean host" is the easy case. The one that goes wrong is the host that already has a
    personal marketplace with a different name and other plugins in it -- installing must not
    rename it, and uninstalling must not empty it.
    """
    path = marketplace_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "name": "personal",
            "interface": {"displayName": "My Marketplace"},
            "plugins": [{"name": "someone-elses-plugin", "source": {"source": "local"}}],
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    install_module.install_local(source, home=home, install=False)
    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["name"] == "personal"
    assert after["interface"] == {"displayName": "My Marketplace"}
    assert after["plugins"] == [{"name": "someone-elses-plugin", "source": {"source": "local"}}]


def test_uninstalling_twice_after_a_reinstall_is_still_clean(source, home):
    for _ in range(2):
        install_module.install_local(source, home=home, install=False)
        uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)
    final = uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)

    assert final.removed == []
    assert not (home / "plugins" / PLUGIN).exists()
    assert json.loads(marketplace_path(home).read_text(encoding="utf-8"))["plugins"] == []


def test_enabling_survives_a_reinstall_because_state_is_not_installer_output(source, home,
                                                                            tmp_path,
                                                                            monkeypatch):
    # Per-repository enablement lives in the state directory, not in the install. A reinstall
    # that silently disabled the reviewer in every repository would be a nasty surprise on a
    # routine update.
    from reviewer_codex import state

    state_dir = tmp_path / "state"
    monkeypatch.setenv("REVIEWER_CODEX_STATE_DIR", str(state_dir))
    repo = tmp_path / "repo"
    repo.mkdir()
    import subprocess as sp
    sp.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "reviewerAgent.md").write_text("be strict\n", encoding="utf-8")
    state.set_feature(repo, "reviewer")

    install_module.install_local(source, home=home, install=False)
    uninstall_module.uninstall_local(source, home=home, run_command=lambda cmd: None)
    install_module.install_local(source, home=home, install=False)

    assert state.feature_enabled(repo, "reviewer")
