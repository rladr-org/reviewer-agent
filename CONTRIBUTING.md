# Contributing

This is a beta maintained by one person. Bug reports with a reproduction are the most useful
contribution; pull requests are welcome but please open an issue first for anything larger than
a fix, so nobody writes code that a design decision has already ruled out.

## Reporting

Use the issue templates: defect, installation failure, platform compatibility, or documentation.
Always include the host and version (`claude --version`, `codex --version`), the backend (Codex
or Pi), your OS, and what `/reviewerModel` reports. For anything security-sensitive, use private
vulnerability reporting instead — see [`SECURITY.md`](SECURITY.md).

## Running the suites

Two independent Python suites, one canonical command each:

```bash
python3 -m pytest -p no:cacheprovider                      # from the repository root
cd plugins/reviewer-agent-codex && python3 -m pytest -p no:cacheprovider
```

Install the development dependencies first with `pip install -r requirements-dev.txt`; the
installed runtime itself is standard-library-only on both hosts, and it needs to stay that way.

The Pi extension has its own:

```bash
cd pi-extensions/git-read-only
npm ci
node test/run.mjs
PI_GIT_READ_LIVE=1 node test/live.mjs
```

Structural checks, run from the repository root:

```bash
python3 tools/check_split_layering.py     # the module layering stays acyclic
python3 tools/gen_facade_contract.py      # regenerate the facade contract after adding a name
python3 tools/release_versions.py         # every declared version agrees
python3 tools/scan_release.py             # nothing private is about to be published
```

## How changes are expected to arrive

- **A test first.** Every behavioural change lands with the smallest regression test that fails
  before it and passes after. The test's name should say what breaks if the behaviour regresses.
- **Comments explain why, not what.** This codebase's comments carry the evidence behind a
  decision — the failure that motivated it, the measurement that settled it. Match that.
- **One concern per commit.** Behavioural changes do not travel with documentation sweeps or
  licence churn.
- **Both suites green.** Plus the structural checks above when you touched what they cover.

## Repository conventions

- Runtime code is standard-library-only. Development dependencies belong in
  `requirements-dev.txt`.
- Python 3.9 is the floor, because macOS ships `/usr/bin/python3` at 3.9 and installation must
  not require installing Python first. No 3.10+ syntax.
- The two packages are independent and neither imports the other. Where they genuinely need the
  same algorithm (process-tree termination, host-version floors), the duplication is deliberate
  and each copy says so.
- `docs/plans/` is tracked and ships publicly; `docs/internal/` never ships. What reaches a
  release is decided by the explicit list in `tools/release_export.py`, not by `.gitignore`.

## Privacy scanning

`tools/scan_release.py` runs in CI over tracked content and over the generated export. It rejects
home paths, email addresses, credential shapes, build artefacts and runtime state, and permits
two documented fixtures: the synthetic project path under `/Users/x/` that the suite uses
throughout, and `example.com` addresses.

Maintainers can add a local, untracked deny list at `tools/private_terms.txt` — one term per
line — for strings specific to their machine or organisation. It is ignored by Git and excluded
from the export on purpose: a list of terms that must never be published cannot itself be
published, and findings from it are redacted in scanner output.

## Release procedure

Version identity lives in one place, `tools/release_versions.py`, and every declared version is
checked against it. The Codex plugin additionally carries `+codex.<timestamp>` build metadata as
a cachebuster; bump it with `python3 tools/release_cachebuster.py`, never by hand. Reinstalling
the Codex plugin needs the local marketplace name, which `python3 tools/read_marketplace_name.py`
prints. Both wrappers prefer the plugin-creator tooling when it is installed and otherwise
perform the same documented transform, so the procedure works in CI and on a contributor's
machine.
