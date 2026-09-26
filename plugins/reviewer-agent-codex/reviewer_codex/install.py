# SPDX-License-Identifier: MPL-2.0
"""Local installation support for the Codex plugin checkout."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from .host_config import HostConfigError, ensure_direct_namespace


class InstallError(RuntimeError):
    """Raised when local installation would overwrite unrelated state."""


@dataclass(frozen=True)
class InstallResult:
    marketplace_path: Path
    plugin_id: str
    source_link: Path


CommandRunner = Callable[[List[str]], None]
HostConfigurer = Callable[[], None]


def _run_command(command: List[str]) -> None:
    subprocess.run(command, check=True)


def verify_hook_python() -> str:
    """Check the interpreter command used by hooks, not just the installer Python."""
    executable = shutil.which("python3")
    message = "Hooks require Python 3.9+ as python3 on the Codex launch PATH."
    if not executable:
        raise InstallError(message)
    try:
        result = subprocess.run(
            [executable, "-c", "import sys; print('.'.join(str(part) for part in sys.version_info[:3]))"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InstallError(message) from exc
    try:
        version = tuple(int(part) for part in result.stdout.strip().split("."))
    except ValueError:
        version = ()
    if result.returncode != 0 or len(version) != 3 or version < (3, 9, 0):
        raise InstallError(message + " Check " + executable)
    return executable


def _read_plugin_name(source: Path) -> str:
    manifest_path = source / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read plugin manifest at {manifest_path}: {exc}") from exc
    name = manifest.get("name") if isinstance(manifest, dict) else None
    if not isinstance(name, str) or not name.strip():
        raise InstallError(f"Plugin manifest at {manifest_path} has no valid name")
    return name


def _ensure_source_link(source: Path, source_link: Path) -> None:
    if source_link.is_symlink() or source_link.exists():
        if source_link.is_symlink() and source_link.resolve() == source:
            return
        raise InstallError(
            f"{source_link} already exists and does not point to {source}; "
            "move it aside or remove it explicitly before installing"
        )
    source_link.parent.mkdir(parents=True, exist_ok=True)
    source_link.symlink_to(source, target_is_directory=True)


def _marketplace_entry(plugin_name: str) -> dict:
    return {
        "name": plugin_name,
        "source": {
            "source": "local",
            "path": f"./plugins/{plugin_name}",
        },
        "policy": {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }


def _load_marketplace(marketplace_path: Path) -> dict:
    if not marketplace_path.exists():
        return {
            "name": "personal",
            "interface": {"displayName": "Personal"},
            "plugins": [],
        }
    try:
        marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InstallError(f"Cannot read marketplace at {marketplace_path}: {exc}") from exc
    if not isinstance(marketplace, dict):
        raise InstallError(f"Marketplace at {marketplace_path} must contain a JSON object")
    name = marketplace.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InstallError(f"Marketplace at {marketplace_path} has no valid name")
    plugins = marketplace.setdefault("plugins", [])
    if not isinstance(plugins, list):
        raise InstallError(f"Marketplace at {marketplace_path} has a non-array plugins field")
    return marketplace


def _write_marketplace(marketplace_path: Path, marketplace: dict, plugin_name: str) -> None:
    entry = _marketplace_entry(plugin_name)
    plugins = marketplace["plugins"]
    matching_indexes = [
        index
        for index, existing in enumerate(plugins)
        if isinstance(existing, dict) and existing.get("name") == plugin_name
    ]
    if matching_indexes:
        plugins[matching_indexes[0]] = entry
        for index in reversed(matching_indexes[1:]):
            del plugins[index]
    else:
        plugins.append(entry)

    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = marketplace_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(marketplace_path)


def _restore_parked_cache_versions(
    cache_root: Path,
    parking_root: Path,
    versions: List[str],
    preserve_replacements: bool = False,
) -> None:
    cache_root.mkdir(parents=True, exist_ok=True)
    for version in versions:
        parked = parking_root / version
        target = cache_root / version
        if preserve_replacements and (target.exists() or target.is_symlink()):
            # A successful install produced a same-version replacement. Keep it at
            # the canonical path for new threads, while the parked directory's inode
            # remains valid for an already-running MCP server.
            continue
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        parked.rename(target)


def _park_cache_versions(cache_root: Path, parking_root: Path) -> List[str]:
    if not cache_root.is_dir():
        return []
    versions = []
    parking_root.mkdir(parents=True, exist_ok=False)
    try:
        for version_path in cache_root.iterdir():
            if not version_path.is_dir() or version_path.name.startswith("."):
                continue
            version_path.rename(parking_root / version_path.name)
            versions.append(version_path.name)
    except Exception:
        _restore_parked_cache_versions(cache_root, parking_root, versions)
        if parking_root.is_dir():
            parking_root.rmdir()
        raise
    return versions


def install_local(
    source: Path,
    *,
    home: Optional[Path] = None,
    run_command: CommandRunner = _run_command,
    configure_host: Optional[HostConfigurer] = None,
    install: bool = True,
) -> InstallResult:
    """Expose a checkout through the personal marketplace and install it."""

    source = source.expanduser().resolve()
    home = (home or Path.home()).expanduser().resolve()
    plugin_name = _read_plugin_name(source)
    verify_hook_python()
    source_link = home / "plugins" / plugin_name
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"

    _ensure_source_link(source, source_link)
    marketplace = _load_marketplace(marketplace_path)
    _write_marketplace(marketplace_path, marketplace, plugin_name)

    plugin_id = f"{plugin_name}@{marketplace['name']}"
    if install:
        cache_root = (
            home
            / ".codex"
            / "plugins"
            / "cache"
            / marketplace["name"]
            / plugin_name
        )
        cache_root.parent.mkdir(parents=True, exist_ok=True)
        parking_root = cache_root.parent / (
            ".{}-live-{}".format(plugin_name, uuid.uuid4().hex)
        )
        cached_versions = _park_cache_versions(cache_root, parking_root)
        installed = False
        try:
            run_command(["codex", "plugin", "add", plugin_id])
            installed = True
        finally:
            _restore_parked_cache_versions(
                cache_root,
                parking_root,
                cached_versions,
                preserve_replacements=installed,
            )
            if parking_root.is_dir():
                try:
                    parking_root.rmdir()
                except OSError:
                    # Same-version replacements intentionally leave the displaced live
                    # inode parked outside the cache root rather than orphaning it.
                    pass
        try:
            (configure_host or (lambda: ensure_direct_namespace(home=home)))()
        except HostConfigError as exc:
            raise InstallError(
                "Plugin installed, but Codex direct-tool configuration failed: {}".format(exc)
            ) from exc
    return InstallResult(
        marketplace_path=marketplace_path,
        plugin_id=plugin_id,
        source_link=source_link,
    )
