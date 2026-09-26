# SPDX-License-Identifier: MPL-2.0
"""Neither the plugin nor its tests may require a particular git version (operator policy).

Ubuntu 20.04 ships git 2.25.1. These flags arrived later and, on 2.25, either print usage
(`init -b`, 2.28) or are echoed back with exit 0 (`--path-format`, 2.31) -- the second silently
corrupts a result instead of failing."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_POST_225 = re.compile(r"""init["',\s-]+(?:q["',\s]+)?-b\b|--initial-branch|--path-format""")


def test_no_file_uses_git_flags_newer_than_2_25():
    offenders = []
    for path in list(ROOT.glob("*.py")) + list((ROOT / "tests").glob("*.py")) + \
            list((ROOT / "tools").glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _POST_225.search(line):
                offenders.append("{}:{}: {}".format(path.relative_to(ROOT), number, line.strip()))
    assert not offenders, "git >2.25 flags found:\n" + "\n".join(offenders)
