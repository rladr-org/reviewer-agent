# SPDX-License-Identifier: MPL-2.0
"""`pytest -m smoke` is the install-time check that AGENT_INSTALL.md step 3 is to run in place of
the full suite (plan Task 20 switches the guide over): under a minute, covering imports,
distribution shape, host versions, the hook launcher and one real-git gate round trip (#5: the
full suite took 5-12 minutes and outran the install agent's command timeout)."""
from pathlib import Path

TESTS = Path(__file__).resolve().parent
REQUIRED = {"test_facade_contract.py", "test_repository_distribution.py",
            "test_host_versions.py", "test_commit_request.py", "test_commit_intent.py",
            "test_hook_launcher.py", "test_gate_owns_commit.py",
            "test_reviewer_cmd_dispatcher.py"}


def test_the_smoke_subset_covers_the_install_critical_suites():
    marked = {p.name for p in TESTS.glob("test_*.py")
              if "pytestmark = pytest.mark.smoke" in p.read_text(encoding="utf-8")}
    assert REQUIRED <= marked, REQUIRED - marked


def test_the_marker_is_registered():
    assert "smoke:" in (TESTS.parent / "pytest.ini").read_text(encoding="utf-8")
