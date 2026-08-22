# SPDX-License-Identifier: MPL-2.0
"""MPL-2.0, applied consistently, with no published contact address.

Two failure modes this pins, both of which are invisible until someone downstream cares:

  MIXED LICENSING. Two packages, two LICENSE files and five metadata identifiers describe the
  terms. A release that changed some of them ships a repository whose own metadata disagrees
  about what it is, and the identifier -- not the LICENSE text -- is what package managers,
  dependency scanners and GitHub's own detection read first.

  A PUBLISHED EMAIL. The manifests carried a working business address. Publishing it is
  irreversible in the way an email address is irreversible: it is scraped within days.

Source headers are SPDX lines rather than repeated Exhibit A blocks, which MPL 2.0 permits when
the notice lives somewhere a recipient would look -- here, the two LICENSE files.
"""
import json
import re
import subprocess
from pathlib import Path

import pytest
from repo_context import requires_repository  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CODEX = ROOT / "plugins" / "reviewer-agent-codex"
SPDX = "SPDX-License-Identifier: MPL-2.0"

# Extensions whose files carry the SPDX header. JSON and Markdown cannot hold a comment safely
# and rely on the LICENSE file; lockfiles and third-party trees are never annotated.
COMMENTED = {".py", ".ts", ".mjs", ".yaml", ".toml", ".ini"}


def tracked_files():
    out = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split()
    return [name for name in out if "node_modules/" not in name]


@pytest.mark.parametrize("relative", ["LICENSE", "plugins/reviewer-agent-codex/LICENSE"])
def test_both_license_files_carry_the_unmodified_mpl_text(relative):
    text = (ROOT / relative).read_text(encoding="utf-8")

    assert text.startswith("Mozilla Public License Version 2.0")
    # The notice-location clause is what makes SPDX-only source headers conformant, and Exhibit
    # A is the notice itself. Both must survive any future edit of this file. Matched on
    # collapsed whitespace, because the canonical text is hard-wrapped mid-clause.
    collapsed = " ".join(text.split())
    assert "Exhibit A - Source Code Form License Notice" in text
    assert "such as a LICENSE file in a relevant directory" in collapsed
    assert not re.search(r"\bMIT\b", text)


def test_every_metadata_identifier_says_mpl():
    identifiers = {
        ".claude-plugin/plugin.json": json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["license"],
        # The marketplace's PLUGIN ENTRY, not its metadata block: `claude plugin validate`
        # reports `metadata.license` as an unknown field it ignores at load time, and the plan
        # is explicit that an identifier goes in only where the schema supports it.
        ".claude-plugin/marketplace.json plugin entry": json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )["plugins"][0]["license"],
        ".codex-plugin/plugin.json": json.loads(
            (CODEX / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )["license"],
        "pi package.json": json.loads(
            (ROOT / "pi-extensions" / "git-read-only" / "package.json").read_text(
                encoding="utf-8")
        )["license"],
    }

    assert set(identifiers.values()) == {"MPL-2.0"}, identifiers
    pyproject = (CODEX / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^license = \{ text = "MPL-2.0" \}$', pyproject, re.M), pyproject


def test_no_manifest_publishes_a_contact_address():
    for relative in (
        ".claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugins/reviewer-agent-codex/.codex-plugin/plugin.json",
        "pi-extensions/git-read-only/package.json",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert '"email"' not in text, relative
        # An address, not any "@": npm scopes (`@earendil-works/pi-ai`) are legitimate and
        # everywhere, so the check is for something email-SHAPED.
        assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text), relative


@requires_repository
def test_every_commentable_source_file_carries_the_spdx_header():
    missing = []
    for name in tracked_files():
        if Path(name).suffix not in COMMENTED:
            continue
        if SPDX not in (ROOT / name).read_text(encoding="utf-8"):
            missing.append(name)

    assert missing == [], missing


@requires_repository
def test_the_header_sits_where_an_interpreter_still_works():
    # A header inserted ABOVE a shebang silently stops the file being executable, and a header
    # inserted INSIDE a module docstring changes documented text. Both are easy to do with a
    # bulk edit and hard to notice afterwards.
    for name in tracked_files():
        if Path(name).suffix != ".py":
            continue
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        if not lines:
            continue
        if lines[0].startswith("#!"):
            assert lines[1].strip() == "# " + SPDX, name
        else:
            assert lines[0].strip() == "# " + SPDX, name


def test_lockfiles_and_generated_third_party_content_are_left_alone():
    lockfile = (ROOT / "pi-extensions" / "git-read-only" / "package-lock.json").read_text(
        encoding="utf-8"
    )

    assert SPDX not in lockfile


def test_the_readme_names_the_license_and_the_notice_location():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Mozilla Public License 2.0" in readme
    assert "[`LICENSE`](LICENSE)" in readme
    assert "SPDX-License-Identifier: MPL-2.0" in readme
    # Word-boundary matched: "COMMIT_TIMEOUT" contains the three letters and is not a licence.
    assert not re.search(r"\bMIT\b", readme)


def test_the_marketplace_metadata_block_carries_no_unsupported_license_field():
    # `claude plugin validate` warns "Unknown field 'license'. Claude Code ignores it at load
    # time." An identifier the host ignores is worse than none: it reads as authoritative and
    # is not.
    metadata = json.loads(
        (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )["metadata"]

    assert "license" not in metadata
