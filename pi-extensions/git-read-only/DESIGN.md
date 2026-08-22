# `git_read`: read-only git for a headless reviewer

A [pi](https://github.com/earendil-works/pi) extension registering one tool, `git_read`, that
gives a headless reviewer real git reads with no ability to mutate the repository.

To be clear about why it is needed: **pi is perfectly capable of running a shell.** `pi -t` is an
allowlist, and the reviewer omits bash from it deliberately, for the reason below. This extension
exists to restore the one capability that removal takes away.

It exists for the `pi` reviewer backend of reviewer-agent. The `codex` backend does not need it:
codex has a shell, so it reads git directly.

## Why a custom tool instead of an allowlisted `bash`

The obvious approach -- allow `bash` and restrict commands with a permission config -- was tried
and **rejected after a live reproduction**. An allowlisted read-only command carrying a shell
redirect inside the working directory:

```
git diff --stat HEAD~1 > written_by_a_read_only_review.txt
```

executed and wrote the file in headless mode. The permission surface matched on the command path
and had no read/write distinction, and no "deny any redirect" control existed.

The resolution is structural rather than a longer denylist: **give the reviewer a tool that spawns
`git` with a fixed, validated argv and `shell:false`, and leave `bash` out of the allowlist
entirely.** With no shell there is no redirect class to defend against, and the read-only guarantee
lives in code that can be unit-tested rather than in configuration.

## Surface

```
subcommand : "diff" | "log" | "show" | "status" | "blame" | "rev-parse"
ref        : optional, validated
path       : optional pathspec, validated
maxCount   : optional, log only
staged     : optional, diff only
stat       : optional, diff only
```

`buildGitArgv` maps each subcommand to a fixed argv. It never accepts a flag from the caller --
the dispatch is the only source of truth for which git flags are used:

| subcommand | argv |
|---|---|
| `diff` + `staged` | `git diff --no-ext-diff --no-textconv --cached -p [ref] [-- path]` |
| `diff` | `git diff --no-ext-diff --no-textconv --stat <ref> [-- path]` |
| `show` | `git show --no-ext-diff --no-textconv --stat <ref> [-- path]` |
| `log` | `git log --oneline -n <maxCount\|50> [ref]` |
| `status` | `git status --short` (rejects `ref`/`path`) |
| `blame` | `git blame --no-textconv --abbrev <ref> -- <path>` |
| `rev-parse` | `--git-dir --git-common-dir`, or `--short <ref>` |

Only `diff` with `staged: true` produces a patch. Everything else is `--stat` or a summary, so
the tool cannot be used to dump arbitrary file contents.

## Hardening

Two classes of defect were found by review and both are closed in the shared source, so the tests
and the extension exercise identical behaviour.

**Option injection.** A leading `-` in a ref or path would be parsed by git as an option rather
than an operand -- `--output=/tmp/x` on `diff` or `show` WRITES A FILE. `validateRef` and
`validatePath` reject leading-dash tokens outright, and `buildGitArgv` additionally fences
pathspecs behind `--`. Both are constrained to `[A-Za-z0-9._/@~^=-]`, which excludes whitespace,
`;`, `|`, `` ` ``, `$(`, and redirects.

**Config-driven command execution.** git will run programs named by configuration:
`diff.external`, textconv drivers, pagers, and `core.fsmonitor`. Three layers close this:

- `GIT_SAFE_ENV` points system and global config at `/dev/null` and forces pagers to `cat`;
- `GLOBAL_GIT_HARDENING` splices `--no-pager -c core.fsmonitor=` before every subcommand, which
  is what neutralizes a hook command living in the repository's own `.git/config` where env
  cannot reach;
- `--no-ext-diff --no-textconv` on every diff- or patch-producing subcommand.

**Ambiguous `git` resolution.** `GIT_BINARY` is resolved once at startup, and `resolveGitBinary`
enforces two rules that matter for a tool pointed at a repository it does not trust:

- **relative `PATH` entries are skipped, never resolved.** A `PATH` carrying `.` or
  `node_modules/.bin` would otherwise resolve `./git` against the process cwd — which, for a
  reviewer, is the repository under review. Every hardening above would then be applied faithfully
  to a repo-controlled executable;
- **there is no bare-string fallback.** Returning `"git"` when nothing resolves defers the lookup
  to exec time and to whatever `PATH` the child inherits, which is the same exposure one step
  later. Unresolvable is `null`, and module load throws rather than degrading.

An earlier version did both of the things this now refuses; the rules are pinned by tests.

## Layout and tests

```
pure.mjs   validators, argv builder, hardening constants -- ZERO dependencies
index.ts   pi tool registration and async spawn glue; re-exports pure.mjs
test/run.mjs    unit tests for the pure surface
test/live.mjs   spawns real git in a temp repo, incl. a diff.external no-execute regression
```

The security-critical surface is deliberately in a **dependency-free** module, so it can be
verified without installing anything:

```
node test/run.mjs                      # 55 tests, no install needed
PI_GIT_READ_LIVE=1 node test/live.mjs  # 8 tests; env var REQUIRED, else it skips and exits 0
```

`live.mjs` is env-gated. **Run bare it prints `skipped` and succeeds**, so a green exit from it
alone proves nothing.

`index.ts` needs `typebox` and `@earendil-works/pi-ai`:

```
npm ci
```

`npm ci` rather than `npm install`, so the shipped `package-lock.json` is what resolves. That
lockfile requires **Node 22.19+** (`@earendil-works/pi-ai`); the two test files need only Node 18+
because they import nothing but node builtins and `pure.mjs`.

## Install

Copy or symlink this directory into `~/.pi/agent/extensions/`, which is where pi discovers it. The
reviewer spawns pi with `-na`, so the reviewed repository's own `.pi` resources are ignored and the
extension must be installed at user level.

Note that `pi list` shows only settings-registered packages and will **not** list an extension
discovered this way; its absence there is expected and is not a failed install.
