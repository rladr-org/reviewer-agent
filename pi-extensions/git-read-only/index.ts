// SPDX-License-Identifier: MPL-2.0
// git_read read-only git tool: pi extension registration + spawn glue.
//
// Pure functions (validateRef, validatePath, buildGitArgv, GIT_BINARY) live
// in ./pure.mjs and are unit-tested by test/run.mjs with no dependencies.
// This file re-exports them for convenience and adds the pi-specific tool
// registration and async spawn execution.

import { spawn } from "node:child_process";
import { Type } from "typebox";
import { StringEnum } from "@earendil-works/pi-ai";
import {
  GIT_BINARY,
  validateRef,
  validatePath,
  buildGitArgv,
  GIT_SAFE_ENV,
  GLOBAL_GIT_HARDENING,
  type GitReadSubcommand,
  type GitReadParams,
} from "./pure.mjs";

// Re-export pure functions so callers/tests importing from src.ts still work.
export { GIT_BINARY, validateRef, validatePath, buildGitArgv };
export type { GitReadSubcommand, GitReadParams };

// ---------- git hardening env ----------------------------------------------

/**
 * Environment for git invocations. Neutralizes config-driven command execution
 * (finding #2): GIT_CONFIG_SYSTEM/GLOBAL=/dev/null makes git ignore
 * system + global config entirely (no ~/.gitconfig `diff.external`, textconv,
 * pager, sshCommand, etc.), GIT_CONFIG_NOSYSTEM is the belt on system config,
 * pagers are forced to cat, and prompts are disabled so an untrusted repo can
 * neither run a helper nor hang the reviewer. Repo-LOCAL config is fenced at the
 * argv layer (--no-ext-diff/--no-textconv per subcommand + the global `-c`
 * overrides in GLOBAL_GIT_HARDENING).
 */
function gitEnv(): Record<string, string> {
  return { ...process.env, ...GIT_SAFE_ENV };
}

// ---------- spawn helper (async) -------------------------------------------

// Default cap for non-diff output (status/log/etc.). The staged-diff path
// (commit gate) gets a much larger cap below so a full patch is returned in
// one call; a reviewer must not silently audit a truncated diff.
const MAX_OUTPUT = 32 * 1024;
const MAX_DIFF_OUTPUT = 1024 * 1024; // 1 MiB for diff --cached -p

function diffCap(argv: string[]): number {
  // Raised cap only for the staged full-patch path: argv is
  // [<git>, "diff", "--cached", "-p", ...]. --stat (summary) stays at default.
  const isStagedPatch = argv[1] === "diff" && argv.includes("--cached") && argv.includes("-p");
  return isStagedPatch ? MAX_DIFF_OUTPUT : MAX_OUTPUT;
}

function runGit(argv: string[], cwd: string, signal?: AbortSignal): Promise<{ stdout: string; stderr: string; exitCode: number | null; truncated: boolean; cap: number }> {
  const cap = diffCap(argv);
  // Splice the global hardening in front of the subcommand: [git, ...hardening,
  // <subcommand>, ...]. buildGitArgv keeps the subcommand at argv[1] (used by
  // diffCap/truncation), so hardening is added only for the actual spawn.
  const spawnArgv = [argv[0], ...GLOBAL_GIT_HARDENING, ...argv.slice(1)];
  return new Promise((resolve, reject) => {
    const child = spawn(spawnArgv[0], spawnArgv.slice(1), {
      cwd,
      shell: false,
      env: gitEnv(),
      signal,
    });
    let stdout = "";
    let stderr = "";
    let stdoutCapped = false;
    child.stdout.on("data", (chunk: Buffer) => {
      if (stdout.length < cap) {
        stdout += chunk.toString("utf8").slice(0, cap - stdout.length);
        if (stdout.length >= cap) stdoutCapped = true;
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      if (stderr.length < 4096) stderr += chunk.toString("utf8").slice(0, 4096 - stderr.length);
    });
    child.on("error", reject);
    child.on("close", (code) => {
      if (stdoutCapped) stdout += `\n...[output truncated at ${cap} bytes]...`;
      resolve({ stdout, stderr, exitCode: code, truncated: stdoutCapped, cap });
    });
  });
}

// ---------- pi extension registration -------------------------------------

export default function gitReadOnlyExtension(pi: import("@earendil-works/pi-coding-agent").ExtensionAPI): void {
  pi.registerTool({
    name: "git_read",
    label: "Git (read-only)",
    description: "Run a read-only git subcommand (diff, log, show, status, blame, rev-parse). No shell, no redirect; spawns git with a fixed validated argv. For a commit gate, use {subcommand:'diff', staged:true} to get the full staged patch (-p).",
    promptSnippet: "Read-only git access (diff/log/show/status/blame) via a validated, no-shell wrapper",
    promptGuidelines: [
      "Use git_read for read-only git queries (diff/log/show/status/blame). It cannot commit, push, or write files; it only reads.",
    ],
    parameters: Type.Object({
      subcommand: StringEnum(["diff", "log", "show", "status", "blame", "rev-parse"] as const),
      ref: Type.Optional(Type.String({ description: "git ref, e.g. HEAD~1, main, v1.2.3. Optional for diff when staged:true." })),
      path: Type.Optional(Type.String({ description: "optional pathspec, e.g. src/x.ts" })),
      maxCount: Type.Optional(Type.Integer({ description: "for log: max commits to show (default 50)" })),
      revParse: Type.Optional(StringEnum(["dirs", "short"] as const, { description: "for rev-parse only: \"dirs\" => --git-dir --git-common-dir (worktree detection); \"short\" => --short <ref>. Omit for other subcommands." })),
      staged: Type.Optional(Type.Boolean({ description: "diff only: append --cached (staged patch). Makes ref optional; output is the full patch (-p) by default." })),
      stat: Type.Optional(Type.Boolean({ description: "diff only: emit --stat summary instead of -p. Default false (full patch for staged, --stat for unstaged back-compat)." })),
    }),
    async execute(_toolCallId, params, signal, _onUpdate, ctx) {
      // Coerce empty-string optional params (the model sometimes sends "") to undefined.
      const p = { ...params } as GitReadParams;
      if (p.revParse === "") delete p.revParse;
      if (p.ref === "") delete p.ref;
      if (p.path === "") delete p.path;
      if (p.staged === "") delete p.staged;
      if (p.stat === "") delete p.stat;
      try {
        const argv = buildGitArgv(p.subcommand!, p);
        const cwd = ctx.cwd;
        const { stdout, stderr, exitCode, truncated, cap } = await runGit(argv, cwd, signal);
        const isError = exitCode !== 0;
        let text = isError
          ? `git ${argv.slice(1).join(" ")} exited ${exitCode}\n${stderr || stdout}`
          : stdout;
        // A commit gate must not silently review a truncated diff. Surface
        // truncation loudly and tell the reviewer to page per-file via path.
        if (!isError && truncated && argv[1] === "diff" && argv.includes("-p")) {
          text += `\n\n[TRUNCATED at ${cap} bytes — the staged patch is larger than the cap. ` +
            `Do NOT approve the commit from this partial view. Re-call git_read with ` +
            `subcommand:"diff", staged:true, path:"<file>" to review each file's ` +
            `staged hunk separately before gating.]`;
        }
        return {
          content: [{ type: "text" as const, text }],
          details: { argv, exitCode, truncated, cap },
          isError,
        };
      } catch (e) {
        return {
          content: [{ type: "text" as const, text: `git_read error: ${(e as Error).message}` }],
          details: {},
          isError: true,
        };
      }
    },
  });
}
