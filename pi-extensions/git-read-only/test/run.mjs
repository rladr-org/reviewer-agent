// SPDX-License-Identifier: MPL-2.0
// Pure-function tests for the git_read tool.
// No test framework: plain node:assert, runnable with `node test/run.mjs`.
// These import the pure validator + argv builder from ../pure.mjs.

import { strict as assert } from "node:assert/strict";
import { validateRef, validatePath, buildGitArgv, GIT_BINARY, GIT_SAFE_ENV, GLOBAL_GIT_HARDENING, resolveGitBinary } from "../pure.mjs";

// Read-only hardening the argv builder now bakes into every diff/patch producer
// (finding #2): --no-ext-diff disables a configured external diff helper and
// --no-textconv disables textconv driver commands, so a diff can never execute a
// repo-controlled program. Pathspecs are fenced after `--`, and option-shaped
// (leading '-') refs/paths are rejected (finding #1: option injection).

let passed = 0, failed = 0;
function test(name, fn) {
  return Promise.resolve().then(fn).then(() => { passed++; console.log("ok   -", name); },
    (e) => { failed++; console.log("FAIL -", name, "\n      ", e && e.stack ? e.stack.split("\n").slice(0,2).join("\n") : e); });
}

// ---- validateRef -----------------------------------------------------------
await test("validateRef accepts HEAD~1", () => assert.strictEqual(validateRef("HEAD~1"), "HEAD~1"));
await test("validateRef accepts main",    () => assert.strictEqual(validateRef("main"), "main"));
await test("validateRef accepts v1.2.3",  () => assert.strictEqual(validateRef("v1.2.3"), "v1.2.3"));
await test("validateRef accepts origin/main", () => assert.strictEqual(validateRef("origin/main"), "origin/main"));
await test("validateRef accepts HEAD",    () => assert.strictEqual(validateRef("HEAD"), "HEAD"));
await test("validateRef rejects redirect-shaped HEAD~1 > x", () => {
  assert.throws(() => validateRef("HEAD~1 > x"));
});
await test("validateRef rejects ; metachar", () => {
  assert.throws(() => validateRef("main; rm -rf x"));
});
await test("validateRef rejects $(...) command substitution", () => {
  assert.throws(() => validateRef("$(curl evil)"));
});
await test("validateRef rejects backtick substitution", () => {
  assert.throws(() => validateRef("`whoami`"));
});
await test("validateRef rejects pipe", () => {
  assert.throws(() => validateRef("main | cat"));
});
await test("validateRef rejects & background", () => {
  assert.throws(() => validateRef("main & rm x"));
});
await test("validateRef rejects newline", () => {
  assert.throws(() => validateRef("main\nrm"));
});
// finding #1: option-injection — a leading '-' would be parsed as a git option.
await test("validateRef rejects option-shaped --output= (leading dash)", () => {
  assert.throws(() => validateRef("--output=/tmp/x"));
});
await test("validateRef rejects -p (leading dash)", () => {
  assert.throws(() => validateRef("-p"));
});
await test("validateRef rejects --upload-pack= (leading dash)", () => {
  assert.throws(() => validateRef("--upload-pack=evil"));
});

// ---- validatePath ----------------------------------------------------------
await test("validatePath accepts src/x.ts", () => assert.strictEqual(validatePath("src/x.ts"), "src/x.ts"));
await test("validatePath accepts ../pkg/a.py", () => assert.strictEqual(validatePath("../pkg/a.py"), "../pkg/a.py"));
await test("validatePath rejects redirect-shaped a.ts > b", () => {
  assert.throws(() => validatePath("a.ts > b"));
});
await test("validatePath rejects option-shaped --output= (leading dash)", () => {
  assert.throws(() => validatePath("--output=/tmp/x"));
});

// ---- buildGitArgv ----------------------------------------------------------
await test("buildGitArgv diff HEAD~1 -> exact argv (hardened)", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { ref: "HEAD~1" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--stat", "HEAD~1"]);
});
await test("buildGitArgv log main maxCount10 -> exact argv", () => {
  assert.deepStrictEqual(buildGitArgv("log", { ref: "main", maxCount: 10 }), [GIT_BINARY, "log", "--oneline", "-n", "10", "main"]);
});
await test("buildGitArgv log default maxCount 50 when omitted", () => {
  assert.deepStrictEqual(buildGitArgv("log", {}), [GIT_BINARY, "log", "--oneline", "-n", "50"]);
});
await test("buildGitArgv status -> exact argv, no ref", () => {
  assert.deepStrictEqual(buildGitArgv("status", {}), [GIT_BINARY, "status", "--short"]);
});
await test("buildGitArgv status rejects ref if supplied", () => {
  assert.throws(() => buildGitArgv("status", { ref: "main" }));
});
await test("buildGitArgv blame requires path", () => {
  assert.throws(() => buildGitArgv("blame", { ref: "HEAD" }));
});
await test("buildGitArgv blame HEAD path -> exact argv (hardened, -- fenced)", () => {
  assert.deepStrictEqual(buildGitArgv("blame", { ref: "HEAD", path: "src/x.ts" }), [GIT_BINARY, "blame", "--no-textconv", "--abbrev", "HEAD", "--", "src/x.ts"]);
});
await test("buildGitArgv diff rejects empty ref", () => {
  assert.throws(() => buildGitArgv("diff", {}));
});
await test("buildGitArgv show rejects empty ref", () => {
  assert.throws(() => buildGitArgv("show", {}));
});
await test("buildGitArgv show HEAD~1 -> exact argv (hardened)", () => {
  assert.deepStrictEqual(buildGitArgv("show", { ref: "HEAD~1" }), [GIT_BINARY, "show", "--no-ext-diff", "--no-textconv", "--stat", "HEAD~1"]);
});
await test("buildGitArgv diff with pathspec -> -- fenced pathspec", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { ref: "HEAD~1", path: "src/x.ts" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--stat", "HEAD~1", "--", "src/x.ts"]);
});

// ---- diff staged / patch mode (commit gate) ------------------------------
await test("diff unstaged default (ref given) keeps --stat for back-compat", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { ref: "HEAD~1" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--stat", "HEAD~1"]);
});
await test("diff staged no ref -> git diff --cached -p (the commit-gate patch)", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { staged: true }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--cached", "-p"]);
});
await test("diff staged with ref -> git diff --cached -p <ref>", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { staged: true, ref: "main" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--cached", "-p", "main"]);
});
await test("diff staged with path -> -- fenced pathspec", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { staged: true, path: "src/x.ts" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--cached", "-p", "--", "src/x.ts"]);
});
await test("diff staged stat:true -> --cached --stat (summary, not patch)", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { staged: true, stat: true }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--stat"]);
});
await test("diff staged stat:true with ref -> --cached --stat <ref>", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { staged: true, stat: true, ref: "HEAD~1" }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--cached", "--stat", "HEAD~1"]);
});
await test("diff unstaged stat:true (no staged) -> --stat when stat requested", () => {
  assert.deepStrictEqual(buildGitArgv("diff", { ref: "HEAD~1", stat: true }), [GIT_BINARY, "diff", "--no-ext-diff", "--no-textconv", "--stat", "HEAD~1"]);
});
await test("diff staged ref still validateRef'd (rejects redirect)", () => {
  assert.throws(() => buildGitArgv("diff", { staged: true, ref: "HEAD > x" }));
});

// ---- finding #1 regression: option injection through ref/path -------------
await test("diff staged path '--output=/tmp/x' is REJECTED (no file write)", () => {
  assert.throws(() => buildGitArgv("diff", { staged: true, path: "--output=/tmp/x" }));
});
await test("diff staged ref '--output=/tmp/x' is REJECTED", () => {
  assert.throws(() => buildGitArgv("diff", { staged: true, ref: "--output=/tmp/x" }));
});
await test("show ref '-O/tmp/x' is REJECTED", () => {
  assert.throws(() => buildGitArgv("show", { ref: "-O/tmp/x" }));
});
await test("blame path '--output=x' is REJECTED", () => {
  assert.throws(() => buildGitArgv("blame", { ref: "HEAD", path: "--output=x" }));
});
await test("every diff/show/blame argv carries the read-only diff hardening", () => {
  const producers = [
    ["diff", { ref: "HEAD~1" }],
    ["diff", { staged: true }],
    ["show", { ref: "HEAD~1" }],
  ];
  for (const [sub, params] of producers) {
    const argv = buildGitArgv(sub, params);
    assert.ok(argv.includes("--no-ext-diff"), `${sub} missing --no-ext-diff`);
    assert.ok(argv.includes("--no-textconv"), `${sub} missing --no-textconv`);
  }
  assert.ok(buildGitArgv("blame", { ref: "HEAD", path: "a" }).includes("--no-textconv"), "blame missing --no-textconv");
});

// ---- finding #2: invocation hardening neutralizes config-driven execution --
await test("GIT_SAFE_ENV nulls global+system config; GLOBAL_GIT_HARDENING kills pager+fsmonitor", () => {
  assert.strictEqual(GIT_SAFE_ENV.GIT_CONFIG_GLOBAL, "/dev/null");
  assert.strictEqual(GIT_SAFE_ENV.GIT_CONFIG_SYSTEM, "/dev/null");
  assert.strictEqual(GIT_SAFE_ENV.GIT_CONFIG_NOSYSTEM, "1");
  assert.ok(GLOBAL_GIT_HARDENING.includes("--no-pager"));
  const i = GLOBAL_GIT_HARDENING.indexOf("-c");
  assert.ok(i >= 0 && GLOBAL_GIT_HARDENING[i + 1] === "core.fsmonitor=", "fsmonitor neutralized");
});

// ---- rev-parse -------------------------------------------------------------
await test("buildGitArgv rev-parse --git-dir --git-common-dir (no ref) -> worktree detection argv", () => {
  assert.deepStrictEqual(buildGitArgv("rev-parse", { revParse: "dirs" }), [GIT_BINARY, "rev-parse", "--git-dir", "--git-common-dir"]);
});
await test("buildGitArgv rev-parse --short ref -> exact argv", () => {
  assert.deepStrictEqual(buildGitArgv("rev-parse", { revParse: "short", ref: "HEAD" }), [GIT_BINARY, "rev-parse", "--short", "HEAD"]);
});
await test("buildGitArgv rev-parse --short requires a ref", () => {
  assert.throws(() => buildGitArgv("rev-parse", { revParse: "short" }));
});
await test("buildGitArgv rev-parse rejects unknown revParse value", () => {
  assert.throws(() => buildGitArgv("rev-parse", { revParse: "--abs-gp" }));
});
await test("buildGitArgv rev-parse rejects redirect-shaped ref", () => {
  assert.throws(() => buildGitArgv("rev-parse", { revParse: "short", ref: "HEAD > x" }));
});

// ---- no shell metacharacters ever appear in argv tokens --------------------
await test("no buildGitArgv output token contains shell metacharacters", () => {
  const cases = [
    ["diff", { ref: "HEAD~1" }],
    ["log", { ref: "main", maxCount: 10 }],
    ["show", { ref: "HEAD~1" }],
    ["status", {}],
    ["blame", { ref: "HEAD", path: "src/x.ts" }],
    ["diff", { ref: "HEAD~1", path: "src/x.ts" }],
    ["rev-parse", { revParse: "dirs" }],
    ["rev-parse", { revParse: "short", ref: "HEAD" }],
    ["diff", { staged: true }],
    ["diff", { staged: true, ref: "main" }],
    ["diff", { staged: true, stat: true, ref: "HEAD~1" }],
  ];
  const bad = [">", ";", "&", "|", "`", "$("];
  for (const [sub, params] of cases) {
    const argv = buildGitArgv(sub, params);
    for (const tok of argv) {
      for (const m of bad) assert.ok(!tok.includes(m), `token ${JSON.stringify(tok)} contains ${m}`);
    }
  }
});

// ---- git binary resolution (finding #3: relative PATH entries) --------------
// The resolver used to accept ANY PATH entry and, failing that, return the bare string "git".
// A PATH carrying "." resolves "./git" against the process cwd -- for a reviewer, the repository
// under review -- so a repo-controlled executable would be spawned with all the argv hardening
// faithfully applied to it. The bare-string fallback is the same exposure deferred to exec time.
await test("resolveGitBinary ignores a relative PATH entry", () => {
  assert.strictEqual(resolveGitBinary(".:./bin:node_modules/.bin"), null);
});
await test("resolveGitBinary skips a relative entry and keeps scanning", () => {
  const got = resolveGitBinary(".:/usr/bin:/bin");
  assert.ok(got === null || got.startsWith("/"), "expected absolute or null, got " + got);
});
await test("resolveGitBinary returns null rather than the bare string git", () => {
  assert.strictEqual(resolveGitBinary(""), null);
  assert.strictEqual(resolveGitBinary(undefined), null);
});
await test("resolveGitBinary resolves an absolute entry to an absolute path", () => {
  const got = resolveGitBinary("/usr/bin:/bin");
  assert.ok(got && got.startsWith("/"), "expected an absolute path, got " + got);
});
await test("GIT_BINARY is itself absolute", () => {
  assert.ok(GIT_BINARY.startsWith("/"), "GIT_BINARY is not absolute: " + GIT_BINARY);
});

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
