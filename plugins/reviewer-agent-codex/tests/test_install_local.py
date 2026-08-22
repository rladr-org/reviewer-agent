# SPDX-License-Identifier: MPL-2.0
import json
from pathlib import Path

import pytest

from reviewer_codex.install import InstallError, install_local


PLUGIN_NAME = "reviewer-agent-codex"


def make_plugin(source: Path) -> None:
    manifest = source / ".codex-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"name": PLUGIN_NAME, "version": "0.1.0"}),
        encoding="utf-8",
    )


def test_install_local_creates_personal_marketplace_and_source_link(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)

    commands = []
    result = install_local(
        source, home=home, run_command=commands.append, configure_host=lambda: None
    )

    source_link = home / "plugins" / PLUGIN_NAME
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

    assert source_link.is_symlink()
    assert source_link.resolve() == source.resolve()
    assert marketplace == {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [
            {
                "name": PLUGIN_NAME,
                "source": {
                    "source": "local",
                    "path": f"./plugins/{PLUGIN_NAME}",
                },
                "policy": {
                    "installation": "AVAILABLE",
                    "authentication": "ON_INSTALL",
                },
                "category": "Productivity",
            }
        ],
    }
    assert commands == [["codex", "plugin", "add", f"{PLUGIN_NAME}@personal"]]
    assert result.marketplace_path == marketplace_path
    assert result.plugin_id == f"{PLUGIN_NAME}@personal"


def test_install_local_preserves_other_marketplace_entries(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace_path.parent.mkdir(parents=True)
    marketplace_path.write_text(
        json.dumps(
            {
                "name": "my-plugins",
                "interface": {"displayName": "My Plugins"},
                "plugins": [{"name": "existing-plugin", "source": "./plugins/existing"}],
            }
        ),
        encoding="utf-8",
    )

    commands = []
    install_local(
        source, home=home, run_command=commands.append, configure_host=lambda: None
    )
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

    assert marketplace["name"] == "my-plugins"
    assert marketplace["interface"] == {"displayName": "My Plugins"}
    assert marketplace["plugins"][0]["name"] == "existing-plugin"
    assert marketplace["plugins"][1]["name"] == PLUGIN_NAME
    assert commands == [["codex", "plugin", "add", f"{PLUGIN_NAME}@my-plugins"]]


def test_install_local_is_idempotent(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)

    install_local(
        source,
        home=home,
        run_command=lambda command: None,
        configure_host=lambda: None,
    )
    install_local(
        source,
        home=home,
        run_command=lambda command: None,
        configure_host=lambda: None,
    )
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))

    assert [entry["name"] for entry in marketplace["plugins"]] == [PLUGIN_NAME]


def test_install_local_refuses_to_replace_an_unrelated_source_path(tmp_path):
    source = tmp_path / "checkout"
    other = tmp_path / "other"
    home = tmp_path / "home"
    source.mkdir()
    other.mkdir()
    make_plugin(source)
    source_link = home / "plugins" / PLUGIN_NAME
    source_link.parent.mkdir(parents=True)
    source_link.symlink_to(other, target_is_directory=True)

    with pytest.raises(InstallError, match="already exists"):
        install_local(
            source,
            home=home,
            run_command=lambda command: None,
            configure_host=lambda: None,
        )


def test_install_local_can_prepare_without_invoking_codex(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)

    commands = []
    install_local(source, home=home, run_command=commands.append, install=False)

    assert commands == []


def test_install_local_configures_reviewer_tools_as_direct_after_install(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    events = []

    install_local(
        source,
        home=home,
        run_command=lambda command: events.append(("install", command)),
        configure_host=lambda: events.append(("configure", None)),
    )

    assert events == [
        ("install", ["codex", "plugin", "add", f"{PLUGIN_NAME}@personal"]),
        ("configure", None),
    ]


def test_prepare_only_does_not_change_codex_host_configuration(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    configured = []

    install_local(
        source,
        home=home,
        run_command=lambda command: None,
        configure_host=lambda: configured.append(True),
        install=False,
    )

    assert configured == []


def test_install_local_preserves_cache_used_by_live_threads(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    old_cache = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / PLUGIN_NAME
        / "0.1.0+codex.old"
    )
    old_script = old_cache / "scripts" / "hook_prompt.py"
    old_script.parent.mkdir(parents=True)
    old_script.write_text("# old live-thread hook\n", encoding="utf-8")
    live_directory_inode = old_cache.stat().st_ino

    def replace_installed_cache(command):
        assert command == ["codex", "plugin", "add", f"{PLUGIN_NAME}@personal"]
        import shutil

        shutil.rmtree(old_cache.parent, ignore_errors=True)
        new_script = old_cache.parent / "0.1.0+codex.new" / "scripts" / "hook_prompt.py"
        new_script.parent.mkdir(parents=True)
        new_script.write_text("# newly installed hook\n", encoding="utf-8")

    install_local(
        source,
        home=home,
        run_command=replace_installed_cache,
        configure_host=lambda: None,
    )

    assert old_script.read_text(encoding="utf-8") == "# old live-thread hook\n"
    assert old_cache.stat().st_ino == live_directory_inode
    assert (
        old_cache.parent / "0.1.0+codex.new" / "scripts" / "hook_prompt.py"
    ).read_text(encoding="utf-8") == "# newly installed hook\n"


def test_install_local_keeps_same_version_replacement_for_new_threads(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    cache_root = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / PLUGIN_NAME
    )
    live_cache = cache_root / "0.1.0"
    live_script = live_cache / "scripts" / "hook_stop.py"
    live_script.parent.mkdir(parents=True)
    live_script.write_text("# old live hook\n", encoding="utf-8")
    live_directory_inode = live_cache.stat().st_ino

    def install_same_version(_command):
        new_script = cache_root / "0.1.0" / "scripts" / "hook_stop.py"
        new_script.parent.mkdir(parents=True)
        new_script.write_text("# newly installed hook\n", encoding="utf-8")

    install_local(
        source,
        home=home,
        run_command=install_same_version,
        configure_host=lambda: None,
    )

    assert live_script.read_text(encoding="utf-8") == "# newly installed hook\n"
    preserved = list(
        cache_root.parent.glob(".{}-live-*/0.1.0".format(PLUGIN_NAME))
    )
    assert len(preserved) == 1
    assert preserved[0].stat().st_ino == live_directory_inode
    assert (preserved[0] / "scripts" / "hook_stop.py").read_text(
        encoding="utf-8"
    ) == "# old live hook\n"


def test_install_local_restores_live_thread_cache_when_plugin_add_fails(tmp_path):
    source = tmp_path / "checkout"
    home = tmp_path / "home"
    source.mkdir()
    make_plugin(source)
    old_cache = (
        home
        / ".codex"
        / "plugins"
        / "cache"
        / "personal"
        / PLUGIN_NAME
        / "0.1.0+codex.live"
    )
    old_script = old_cache / "scripts" / "hook_stop.py"
    old_script.parent.mkdir(parents=True)
    old_script.write_text("# immutable live hook\n", encoding="utf-8")

    def interrupted_install(_command):
        import shutil

        shutil.rmtree(old_cache.parent, ignore_errors=True)
        raise RuntimeError("codex plugin add interrupted")

    with pytest.raises(RuntimeError, match="interrupted"):
        install_local(
            source,
            home=home,
            run_command=interrupted_install,
            configure_host=lambda: None,
        )

    assert old_script.read_text(encoding="utf-8") == "# immutable live hook\n"
