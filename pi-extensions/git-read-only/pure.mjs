// SPDX-License-Identifier: MPL-2.0
// Pure, zero-dependency functions for the git_read tool.
// Split from src.ts so the test suite can import them without pi's module
// graph (typebox/@earendil-works/*). The extension's src.ts re-exports these
// and adds the pi-specific registration + spawn glue.

import { spawnSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { delimiter } from "node:path";

// ---------- environment / git binary ---------------------------------------

/**
 * Resolve `git` to an ABSOLUTE path from a PATH-shaped string, or null.
 *
 * Two rules, both load-bearing for a tool whose whole purpose is surviving a hostile repository:
 *
 * 1. **Relative PATH entries are skipped, never resolved.** A PATH carrying `.` or
 *    `node_modules/.bin` would otherwise resolve `./git` against the process cwd -- which, for a
 *    reviewer, is the repository under review. That hands a repo-controlled executable everything
 *    the argv hardening exists to prevent.
 * 2. **No bare-string fallback.** Returning `"git"` when nothing is found defers resolution to
 *    exec time and to whatever PATH the child is given, which is the same exposure one step later.
 *    Unresolvable is null, and the caller fails closed.
 *
 * Pure and PATH-injectable so it can be tested without mutating the process environment.
 * @param {string} pathEnv value of PATH
 * @returns {string|null} absolute path to git, or null if none was found
 */
export function resolveGitBinary(pathEnv) {
  for (const dir of String(pathEnv || "").split(delimiter)) {
    if (!dir || !dir.startsWith("/")) continue;   // rule 1: absolute entries only
    const cand = dir + "/git";
    try {
      if (existsSync(cand) && statSync(cand).isFile()) return cand;
    } catch {
      /* keep scanning */
    }
  }
  return null;                                     // rule 2: no bare-string fallback
}

/**
 * Absolute path to the git binary, resolved once at startup.
 * @throws at module load if git cannot be resolved absolutely -- a git tool that cannot find git
 * has nothing to degrade to, and failing loudly here beats spawning an ambiguous `git` later.
 */
export const GIT_BINARY = (() => {
  const resolved = resolveGitBinary(process.env.PATH);
  if (!resolved) {
    throw new Error(
      "git_read: could not resolve an absolute path to `git` from PATH. " +
      "Relative PATH entries are deliberately ignored; install git or add its absolute directory.");
  }
  return resolved;
})();

// ---------- invocation hardening (shared by index.ts + live tests) ---------

/**
 * Env overrides that neutralize config-driven command execution (finding #2):
 * ignore system + global config entirely (/dev/null), belt on system config,
 * force pagers to cat, and disable prompts. Repo-LOCAL config is fenced by
 * GLOBAL_GIT_HARDENING + the per-subcommand --no-ext-diff/--no-textconv flags.
 * Exported so the live test invokes git exactly as the extension does.
 */
export const GIT_SAFE_ENV = {
  GIT_CONFIG_NOSYSTEM: "1",
  GIT_CONFIG_SYSTEM: "/dev/null",
  GIT_CONFIG_GLOBAL: "/dev/null",
  GIT_PAGER: "cat",
  PAGER: "cat",
  GIT_TERMINAL_PROMPT: "0",
};

/**
 * Global git options spliced before EVERY subcommand at spawn time (not in
 * buildGitArgv, which stays the semantic/operand surface). --no-pager disables
 * any pager; `-c core.fsmonitor=` disables a repo-local fsmonitor hook command
 * (a command-execution vector that env cannot neutralize since it lives in the
 * repo's own .git/config).
 */
export const GLOBAL_GIT_HARDENING = ["--no-pager", "-c", "core.fsmonitor="];

// ---------- validation (pure) ----------------------------------------------

/** A ref/path is one of these chars: letters, digits, and . _ / @ ~ ^ = - */
const SAFE_TOKEN = /^[A-Za-z0-9._/@~^=\-]+$/;

/**
 * Validate a git ref. Must be a single safe token. Rejects shell
 * metacharacters, whitespace, command substitution, pipes, redirects.
 * @returns the ref unchanged (for chaining)
 * @throws Error describing why the ref was rejected
 */
export function validateRef(ref) {
  if (typeof ref !== "string" || ref.length === 0) {
    throw new Error("ref is required and must be a non-empty string");
  }
  // Reject option-shaped tokens: a leading '-' would be parsed by git as an
  // OPTION (e.g. `--output=/tmp/x` on diff/show WRITES a file), not a ref. The
  // SAFE_TOKEN class alone permits '-', so this guard is what closes the
  // option-injection hole; buildGitArgv additionally fences operands with `--`.
  if (ref.startsWith("-")) {
    throw new Error(`ref ${JSON.stringify(ref)} must not start with '-' (git would parse it as an option)`);
  }
  if (!SAFE_TOKEN.test(ref)) {
    throw new Error(`ref ${JSON.stringify(ref)} contains disallowed characters; only [A-Za-z0-9._/@~^=-] are permitted`);
  }
  return ref;
}

/**
 * Validate a pathspec. Same safe-token rules (a relative path like
 * `src/x.ts` or `../pkg/a.py` fits; a redirect target like `a.ts > b` does not).
 */
export function validatePath(path) {
  if (typeof path !== "string" || path.length === 0) {
    throw new Error("path is required and must be a non-empty string");
  }
  // Same option-injection guard as validateRef: a leading '-' pathspec would be
  // read as an option before the `--` fence. Reject it outright.
  if (path.startsWith("-")) {
    throw new Error(`path ${JSON.stringify(path)} must not start with '-' (git would parse it as an option)`);
  }
  if (!SAFE_TOKEN.test(path)) {
    throw new Error(`path ${JSON.stringify(path)} contains disallowed characters; only [A-Za-z0-9._/@~^=-] are permitted`);
  }
  return path;
}

// ---------- argv builder (pure) --------------------------------------------

// (type aliases live in src.ts for the extension; pure.mjs is plain JS)

/**
 * Build a fixed git argv for the given subcommand and params. Never accepts
 * arbitrary flags from the caller; the dispatch is the only source of truth
 * for which git flags are used.
 *
 * @returns argv array suitable for spawn/execFile with shell:false.
 * @throws if required params are missing or disallowed params are present.
 */
export function buildGitArgv(subcommand, params) {
  const ref = params.ref !== undefined && params.ref !== "" ? validateRef(params.ref) : undefined;
  const path = params.path !== undefined && params.path !== "" ? validatePath(params.path) : undefined;
  const maxCount = params.maxCount;

  // Read-only diff hardening (finding #2): --no-ext-diff disables any
  // configured external diff helper (repo/global/system `diff.external`) and
  // --no-textconv disables textconv driver commands, so a diff/patch can never
  // execute a repo-controlled program. Applied to every diff/patch producer.
  const DIFF_SAFE = ["--no-ext-diff", "--no-textconv"];

  switch (subcommand) {
    case "diff": {
      const staged = params.staged === true;
      const stat = params.stat === true;
      if (staged) {
        // Staged mode (commit gate): --cached, ref optional, default -p.
        const out = stat ? "--stat" : "-p";
        const argv = [GIT_BINARY, "diff", ...DIFF_SAFE, "--cached", out];
        if (ref) argv.push(ref);
        if (path) argv.push("--", path);
        return argv;
      }
      // Unstaged default (back-compat): requires a ref, emits --stat.
      if (!ref) throw new Error("diff requires a ref (or staged:true for the staged patch)");
      const argv = [GIT_BINARY, "diff", ...DIFF_SAFE, "--stat", ref];
      if (path) argv.push("--", path);
      return argv;
    }
    case "show": {
      if (!ref) throw new Error("show requires a ref");
      const argv = [GIT_BINARY, "show", ...DIFF_SAFE, "--stat", ref];
      if (path) argv.push("--", path);
      return argv;
    }
    case "log": {
      const n = maxCount ?? 50;
      if (!Number.isInteger(n) || n <= 0) throw new Error("maxCount must be a positive integer");
      const argv = [GIT_BINARY, "log", "--oneline", "-n", String(n)];
      if (ref) argv.push(ref);
      return argv;
    }
    case "status": {
      if (ref) throw new Error("status does not accept a ref");
      if (path) throw new Error("status does not accept a path");
      return [GIT_BINARY, "status", "--short"];
    }
    case "blame": {
      if (!ref) throw new Error("blame requires a ref");
      if (!path) throw new Error("blame requires a path");
      // --no-textconv: blame otherwise runs a configured textconv command.
      return [GIT_BINARY, "blame", "--no-textconv", "--abbrev", ref, "--", path];
    }
    case "rev-parse": {
      const rp = params.revParse;
      if (rp === "dirs") {
        // Worktree detection: --git-dir --git-common-dir. Equal => main
        // checkout; differ => linked worktree. Ignores ref/path.
        if (ref) throw new Error("rev-parse dirs does not accept a ref");
        if (path) throw new Error("rev-parse dirs does not accept a path");
        return [GIT_BINARY, "rev-parse", "--git-dir", "--git-common-dir"];
      }
      if (rp === "short") {
        if (!ref) throw new Error("rev-parse short requires a ref");
        return [GIT_BINARY, "rev-parse", "--short", ref];
      }
      throw new Error(`unknown revParse value ${JSON.stringify(rp)}; expected "dirs" or "short"`);
    }
    default:
      throw new Error(`unknown subcommand ${JSON.stringify(subcommand)}`);
  }
}
