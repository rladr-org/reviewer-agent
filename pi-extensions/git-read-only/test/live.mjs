// SPDX-License-Identifier: MPL-2.0
// Live integration test for the git_read tool's execute() path (matrix 13).
// Builds a temp git repo, calls the spawn helper directly, and asserts:
//   - diff on a known commit returns the diff output
//   - bad ref returns isError with non-zero exit
// Env-gated: set PI_GIT_READ_LIVE=1 to run (skipped by default; needs git).
//
// Run: PI_GIT_READ_LIVE=1 node test/live.mjs

import { strict as assert } from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// We test the spawn glue by reusing pure.mjs's buildGitArgv + the SHARED
// invocation hardening (GIT_SAFE_ENV / GLOBAL_GIT_HARDENING) so this exercises
// git exactly as index.ts does (index.ts imports typebox and can't load under
// plain node). Same source of truth => the regression test can't drift.
import { buildGitArgv, GIT_SAFE_ENV, GLOBAL_GIT_HARDENING } from "../pure.mjs";

function runGit(argv, cwd, env) {
  // Splice the global hardening in front of the subcommand, mirroring index.ts.
  const spawnArgv = [argv[0], ...GLOBAL_GIT_HARDENING, ...argv.slice(1)];
  return new Promise((resolve, reject) => {
    const child = spawn(spawnArgv[0], spawnArgv.slice(1), { cwd, shell: false, env });
    let stdout = "", stderr = "";
    child.stdout.on("data", (c) => { stdout += c.toString(); });
    child.stderr.on("data", (c) => { stderr += c.toString(); });
    child.on("error", reject);
    child.on("close", (code) => resolve({ stdout, stderr, exitCode: code }));
  });
}

const env = { ...process.env, ...GIT_SAFE_ENV };

function sh(argv, cwd) {
  return new Promise((res, rej) => {
    const c = spawn(argv[0], argv.slice(1), { cwd, shell: false, env });
    let e = "";
    c.stderr.on("data", (x) => { e += x.toString(); });
    c.on("error", rej);
    c.on("close", (code) => res({ code, e }));
  });
}

async function main() {
  const repo = mkdtempSync(join(tmpdir(), "git-read-live-"));
  try {
    // --- build a tiny repo with two commits ---
    let r = await sh(["git", "init", "-q"], repo); assert.strictEqual(r.code, 0, "git init");
    r = await sh(["git", "config", "user.email", "t@t"], repo); assert.strictEqual(r.code, 0);
    r = await sh(["git", "config", "user.name", "T"], repo); assert.strictEqual(r.code, 0);
    mkdirSync(join(repo, "src"), { recursive: true });
    writeFileSync(join(repo, "src", "a.txt"), "hello\n");
    r = await sh(["git", "add", "."], repo); assert.strictEqual(r.code, 0);
    r = await sh(["git", "commit", "-q", "-m", "c1"], repo); assert.strictEqual(r.code, 0, "commit1 " + r.e);
    writeFileSync(join(repo, "src", "a.txt"), "hello world\n");
    writeFileSync(join(repo, "src", "b.txt"), "new\n");
    r = await sh(["git", "add", "."], repo); assert.strictEqual(r.code, 0);
    r = await sh(["git", "commit", "-q", "-m", "c2"], repo); assert.strictEqual(r.code, 0, "commit2 " + r.e);

    // --- test 1: diff HEAD~1 returns the c1->c2 diff ---
    const diffArgv = buildGitArgv("diff", { subcommand: "diff", ref: "HEAD~1" });
    const diff = await runGit(diffArgv, repo, env);
    assert.strictEqual(diff.exitCode, 0, "diff exit 0");
    assert.ok(diff.stdout.includes("b.txt"), "diff mentions new file b.txt");
    assert.ok(diff.stdout.includes("a.txt"), "diff mentions changed a.txt");
    console.log("ok   - live diff HEAD~1 returns expected diff output");

    // --- test 2: bad ref -> non-zero exit (isError path) ---
    const badArgv = buildGitArgv("show", { subcommand: "show", ref: "no-such-ref-xyz" });
    const bad = await runGit(badArgv, repo, env);
    assert.notStrictEqual(bad.exitCode, 0, "bad ref must error");
    console.log("ok   - live show with bad ref returns non-zero exit");

    // --- test 3: log returns commit lines ---
    const logArgv = buildGitArgv("log", { subcommand: "log", maxCount: 5 });
    const log = await runGit(logArgv, repo, env);
    assert.strictEqual(log.exitCode, 0);
    assert.ok(log.stdout.includes("c1") && log.stdout.includes("c2"), "log lists both commits");
    console.log("ok   - live log -n 5 returns both commits");

    // --- test 4: status --short on dirty tree ---
    writeFileSync(join(repo, "src", "c.txt"), "dirty\n");
    const stArgv = buildGitArgv("status", { subcommand: "status" });
    const st = await runGit(stArgv, repo, env);
    assert.strictEqual(st.exitCode, 0);
    assert.ok(st.stdout.includes("c.txt"), "status shows untracked c.txt");
    console.log("ok   - live status --short shows untracked file");

    // --- test 5: rev-parse --git-dir --git-common-dir (main checkout: equal) ---
    const rpArgv = buildGitArgv("rev-parse", { subcommand: "rev-parse", revParse: "dirs" });
    const rp = await runGit(rpArgv, repo, env);
    assert.strictEqual(rp.exitCode, 0);
    const [gitDir, gitCommonDir] = rp.stdout.trim().split("\n");
    assert.ok(gitDir && gitCommonDir, `rev-parse dirs returned two lines: ${rp.stdout}`);
    assert.strictEqual(gitDir, gitCommonDir, "main checkout: --git-dir == --git-common-dir");
    console.log("ok   - live rev-parse dirs on main checkout returns equal dirs");

    // --- test 6: rev-parse --short HEAD resolves the commit ---
    const shortArgv = buildGitArgv("rev-parse", { subcommand: "rev-parse", revParse: "short", ref: "HEAD" });
    const short = await runGit(shortArgv, repo, env);
    assert.strictEqual(short.exitCode, 0);
    assert.ok(/^[0-9a-f]{7,}$/.test(short.stdout.trim()), `rev-parse --short HEAD returns a short SHA: ${short.stdout}`);
    console.log("ok   - live rev-parse --short HEAD returns a short SHA");

    // --- test 7: staged full patch (commit gate path) ---
    // Stage a change and confirm git diff --cached -p returns real hunks.
    writeFileSync(join(repo, "src", "a.txt"), "hello world (staged edit)\n");
    writeFileSync(join(repo, "src", "staged_new.txt"), "new staged file\n");
    await sh(["git", "add", "src/a.txt", "src/staged_new.txt"], repo);
    const stagedArgv = buildGitArgv("diff", { subcommand: "diff", staged: true });
    const stagedDiff = await runGit(stagedArgv, repo, env);
    assert.strictEqual(stagedDiff.exitCode, 0, "staged diff exit 0");
    assert.ok(stagedDiff.stdout.includes("diff --git a/src/a.txt"), "staged diff has a.txt hunk");
    assert.ok(stagedDiff.stdout.includes("+new staged file"), "staged diff has staged_new.txt hunk");
    // Sanity: the staged argv carries the read-only diff hardening.
    assert.deepStrictEqual(stagedArgv, [stagedArgv[0], "diff", "--no-ext-diff", "--no-textconv", "--cached", "-p"]);
    console.log("ok   - live diff staged:true returns full staged patch (-p hunks)");
    // Clean: unstage so the temp repo is left tidy.
    await sh(["git", "reset", "-q"], repo);

    // --- test 8 (finding #2 regression): repo-local diff.external MUST NOT run ---
    // Set a repo-local external diff helper that writes a marker file, then run
    // the staged diff. --no-ext-diff + GIT_SAFE_ENV must keep git on its builtin
    // diff so the helper never executes (an untrusted repo cannot get RCE).
    const marker = join(repo, "PWNED_diff_external");
    const helper = join(repo, "evil.sh");
    writeFileSync(helper, `#!/bin/sh\ntouch '${marker}'\n`);
    await sh(["chmod", "+x", helper], repo);
    await sh(["git", "config", "diff.external", helper], repo); // repo-local .git/config
    writeFileSync(join(repo, "src", "a.txt"), "trigger external diff\n");
    await sh(["git", "add", "src/a.txt"], repo);
    const extArgv = buildGitArgv("diff", { subcommand: "diff", staged: true });
    const extDiff = await runGit(extArgv, repo, env);
    assert.ok(!existsSync(marker), "diff.external helper MUST NOT execute (--no-ext-diff)");
    assert.strictEqual(extDiff.exitCode, 0, "staged diff still succeeds on builtin diff");
    assert.ok(extDiff.stdout.includes("diff --git"), "builtin diff output present");
    console.log("ok   - live repo-local diff.external does NOT execute (finding #2 fixed)");
    await sh(["git", "config", "--unset", "diff.external"], repo);
    await sh(["git", "reset", "-q"], repo);

    console.log("\n8 live tests passed");
  } finally {
    rmSync(repo, { recursive: true, force: true });
  }
}

if (process.env.PI_GIT_READ_LIVE === "1") {
  main().catch((e) => { console.error("LIVE TEST FAILURE:", e); process.exit(1); });
} else {
  console.log("skipped (set PI_GIT_READ_LIVE=1 to run)");
}
