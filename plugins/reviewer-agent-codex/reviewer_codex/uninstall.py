# SPDX-License-Identifier: MPL-2.0
"""Remove this plugin's configuration, and only this plugin's configuration.

Installation writes into four places that all belong to other tools as well: the personal
marketplace manifest, `~/plugins/`, the Codex plugin cache, and the Codex config's
`direct_only_tool_namespaces` array. Removal therefore edits shared state, which is where a
tidy-up does damage: taking a neighbour's marketplace entry, symlink or namespace with it is
worse than leaving something behind, because it is discovered much later and cannot be
attributed.

Three rules follow, and the tests pin all three:

  ONLY OURS. Every removal is matched by name and, for the symlink, by target. A path under our
  name that points somewhere else is somebody else's and stays.
  IDEMPOTENT. A second run, or a run after a half-finished install, succeeds and reports that
  there was nothing to do. A removal path that only works once is one people abandon mid-repair.
  STATE IS NOT CONFIGURATION. Retained verdicts are the user's data, not installer output, so
  they survive unless `--purge-state` says otherwise.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from .host_config import DIRECT_TOOL_NAMESPACE  # noqa: F401  (re-exported for callers)
from .state import state_home


@dataclass
class UninstallResult:
    removed: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


def _run_command(command: List[str]) -> None:
    subprocess.run(command, check=True)


# A marketplace name and a plugin name are used to BUILD PATHS under the Codex cache, and one
# of those paths is deleted with rmtree. Both come from JSON on disk, so both are validated as
# single, boring path components before any path is built: an absolute value ("/etc") silently
# replaces the whole path when joined, and "../.." walks out of the cache root.
_SAFE_COMPONENT = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _safe_component(value, label):
    text = str(value or "").strip()
    if not _SAFE_COMPONENT.match(text) or text in (".", ".."):
        raise InstallNameError(
            "{} {!r} is not a plain name; refusing to build a path from it".format(label, text)
        )
    return text


class InstallNameError(ValueError):
    """A name read from JSON that must not be turned into a filesystem path."""


def _plugin_name(source: Path) -> str:
    manifest = json.loads(
        (source / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    if not isinstance(manifest, dict):
        raise InstallNameError("plugin manifest at {} is not a JSON object".format(source))
    return _safe_component(manifest.get("name"), "plugin name")


def remove_direct_namespace(namespaces, namespace=DIRECT_TOOL_NAMESPACE):
    """The namespace list with ours removed, preserving order and every other entry."""
    return [value for value in namespaces if value != namespace]


def _remove_marketplace_entry(path: Path, plugin_name: str, result: UninstallResult) -> None:
    if not path.is_file():
        return
    try:
        marketplace = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        result.warnings.append("could not read {}: {}".format(path, error))
        return
    if not isinstance(marketplace, dict):
        result.warnings.append(
            "{} does not contain a JSON object; left untouched".format(path)
        )
        return
    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list):
        return
    remaining = [
        entry for entry in plugins
        if not (isinstance(entry, dict) and entry.get("name") == plugin_name)
    ]
    if len(remaining) == len(plugins):
        return
    marketplace["plugins"] = remaining
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(
            json.dumps(marketplace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as error:
        result.warnings.append("could not rewrite {}: {}".format(path, error))
        return
    # The FILE is never deleted, even when it is now empty: it is the user's marketplace, and
    # this plugin may not have been the one that created it.
    result.removed.append("marketplace entry {} in {}".format(plugin_name, path))


def _remove_source_link(link: Path, source: Path, result: UninstallResult) -> None:
    if not link.is_symlink():
        if link.exists():
            result.warnings.append(
                "{} exists but is not a symlink this installer created; left in place".format(link)
            )
        return
    try:
        target = link.resolve()
    except OSError:
        target = None
    if target is not None and target != source.resolve():
        result.warnings.append(
            "{} points at {}, not this checkout; left in place".format(link, target)
        )
        return
    try:
        link.unlink()
    except OSError as error:
        result.warnings.append("could not remove {}: {}".format(link, error))
        return
    result.removed.append("source link {}".format(link))


def _remove_cache(home: Path, marketplace_name: str, plugin_name: str,
                  result: UninstallResult) -> None:
    cache_root = (home / ".codex" / "plugins" / "cache").resolve()
    cache = cache_root / marketplace_name / plugin_name
    # Belt and braces over the name validation: whatever the components were, the directory
    # about to be deleted has to sit inside the Codex plugin cache. A symlinked component could
    # otherwise still redirect the delete outside it.
    try:
        resolved = cache.resolve()
        resolved.relative_to(cache_root)
    except (OSError, ValueError):
        result.warnings.append(
            "refusing to delete {}: it is not inside {}".format(cache, cache_root)
        )
        return
    if not cache.exists():
        return
    try:
        shutil.rmtree(cache)
    except OSError as error:
        result.warnings.append("could not remove {}: {}".format(cache, error))
        return
    result.removed.append("plugin cache {}".format(cache))


def _marketplace_name(path: Path, result: Optional[UninstallResult] = None) -> str:
    """The marketplace name, or the default when the file is missing, broken or unsafe.

    Falling back rather than raising: an unreadable marketplace must not prevent the rest of
    the cleanup, and the default is what `install_local` would have created.
    """
    try:
        marketplace = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        if path.exists() and result is not None:
            result.warnings.append("could not read {}: {}".format(path, error))
        return "personal"
    if not isinstance(marketplace, dict):
        if result is not None:
            result.warnings.append(
                "{} does not contain a JSON object; assuming the default marketplace".format(path)
            )
        return "personal"
    try:
        return _safe_component(marketplace.get("name") or "personal", "marketplace name")
    except InstallNameError as error:
        if result is not None:
            result.warnings.append("{}; assuming the default marketplace".format(error))
        return "personal"


def uninstall_local(
    source: Path,
    *,
    home: Optional[Path] = None,
    run_command: Callable[[List[str]], None] = _run_command,
    purge_state: bool = False,
) -> UninstallResult:
    """Undo `install_local`. Returns what was removed and what was deliberately left."""
    source = Path(source).expanduser().resolve()
    home = (home or Path.home()).expanduser().resolve()
    plugin_name = _plugin_name(source)
    marketplace_path = home / ".agents" / "plugins" / "marketplace.json"
    result = UninstallResult()
    marketplace_name = _marketplace_name(marketplace_path, result)

    # Ask Codex first, while its marketplace entry still resolves. A failure here is expected
    # on a machine where the CLI is gone or the plugin was never installed, and must not stop
    # the file-level cleanup -- otherwise a broken install can never be cleaned up at all.
    if (home / ".codex" / "plugins" / "cache" / marketplace_name / plugin_name).exists():
        try:
            run_command(["codex", "plugin", "remove", "{}@{}".format(plugin_name,
                                                                     marketplace_name)])
            result.removed.append("installed plugin {}@{}".format(plugin_name, marketplace_name))
        except (OSError, subprocess.SubprocessError) as error:
            result.warnings.append(
                "could not ask codex to remove {}@{} ({}); removing its files "
                "directly".format(plugin_name, marketplace_name, error)
            )

    _remove_cache(home, marketplace_name, plugin_name, result)
    _remove_marketplace_entry(marketplace_path, plugin_name, result)
    _remove_source_link(home / "plugins" / plugin_name, source, result)

    python_path = home / ".codex" / "reviewer-agent-codex" / "python-path"
    try:
        if python_path.exists() or python_path.is_symlink():
            python_path.unlink()
            result.removed.append("Python interpreter selection {}".format(python_path))
    except OSError as error:
        result.warnings.append("could not remove {}: {}".format(python_path, error))

    if purge_state:
        state = state_home()
        if state.exists():
            try:
                shutil.rmtree(state)
                result.removed.append("reviewer state {}".format(state))
            except OSError as error:
                result.warnings.append("could not remove {}: {}".format(state, error))
    return result
