# SPDX-License-Identifier: MPL-2.0
"""Acceptance check for the reviewer_lib split: the layering must stay ACYCLIC.

TWO MODES, selected by what exists on disk, because one check cannot cover both sides of a
migration and a checker that silently measures the wrong thing is worse than no checker:

  PLAN mode   -- no target module exists yet. Assigns every top-level function and constant in
                 the monolithic reviewer_lib.py to its target module BY ROLE, then walks real
                 AST call/name references to derive the cross-module edges the split WOULD have.
                 Validates the plan before anything moves.

  VERIFY mode -- at least one target module exists. Ignores the line bands entirely and reads
                 the ACTUAL `import` statements of the target modules and the facade. This is
                 the only thing that can catch a circular import introduced during extraction;
                 the bands are meaningless once code has moved, and a band-based pass after the
                 first extraction would be measuring a file that no longer holds the code.

Both modes fail on any edge pointing FORWARD in the declared layer order. The mode actually run
is printed, so a pass can never be mistaken for the check you meant to run.

Role beats file position: the monolith's banner sections are a first approximation, and the
symbols in OVERRIDE/CONST_OVERRIDE are the ones whose position contradicts what they do.
"""
import ast
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(HERE, "reviewer_lib.py")

ORDER = ["base", "state", "transcript", "git", "manifest", "prompts", "backends", "stopcontinue"]
MODULE_FILE = {m: os.path.join(HERE, "reviewer_{}.py".format(m)) for m in ORDER}

BANDS = [(0, 1018, "state"), (1018, 1031, "state"), (1031, 1222, "git"),
         (1222, 1425, "backends"), (1425, 1586, "prompts"), (1586, 1880, "backends"),
         (1880, 2779, "prompts"), (2779, 2943, "stopcontinue"), (2943, 3761, "manifest"),
         (3761, 99999, "stopcontinue")]

OVERRIDE = {
    "_transcript_events": "transcript", "_event_content": "transcript",
    "_event_text": "transcript", "_is_human_prompt": "transcript",
    "_event_epoch_seconds": "transcript", "_reset_transcript_cache": "transcript",
    "resolve_transcript_path": "transcript", "_parse_transcript_events": "transcript",
    "short": "base", "tail": "base", "_sanitize": "base", "log": "base",
    "_atomic_write_text": "base", "_publish_if_absent": "base",
    "reviewer_home": "base", "reviewer_state_home": "base",
    "brief_capture_argv": "backends", "_brief_capture_run": "backends",
    "brief_and_capture_text": "backends", "parse_session_id": "backends",
    "resume_by_id_argv": "backends", "run_codex_resume_by_id": "backends",
    "pi_session_file": "state", "read_pi_session": "state", "write_pi_session": "state",
    "write_pi_session_if_absent": "state", "last_review_file": "state",
    "session_brief_file": "state", "record_session_brief": "state", "session_brief": "state",
}
CONST_OVERRIDE = {
    "COMMIT_PROMPT": "prompts", "PI_COMMIT_PROMPT": "prompts",
    "STOP_SUPERVISE_PROMPT": "prompts", "DEFAULT_BRIEF": "prompts",
    "APPROVAL_CONFIG": "backends", "SERVICE_TIER_CONFIG": "backends",
    "BRIEF_REASONING_CONFIG": "backends", "BRIEF_CAPTURE_TIMEOUT": "backends",
    "TIMEOUT_EXIT": "backends", "DEFAULT_TIMEOUT": "backends",
    "_LAST_INVOCATION": "backends", "LOG_ENTRY_MAX": "base",
    "_TASK_NOTIFICATION_TUID": "transcript", "_NON_HUMAN_USER_PREFIXES": "transcript",
    "_TRANSCRIPT_CACHE": "transcript",
    "_COMMIT_RE": "git", "_COMMIT_VALUE_SHORT": "git", "_COMMIT_VALUE_LONG": "git",
    "_GIT_GLOBAL_VALUE": "git", "_SHELL_OPS": "git", "_REDIR_RE": "git",
}


def _band(ln):
    for a, b, m in BANDS:
        if a <= ln < b:
            return m
    return "state"


def plan_edges():
    """Edges the split WOULD produce, derived from the monolith by role."""
    tree = ast.parse(open(LIB, encoding="utf-8").read())
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    consts = {n.targets[0].id: n.lineno for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id.isupper()}
    fmod = {n: OVERRIDE.get(n, _band(f.lineno)) for n, f in funcs.items()}
    cmod = {c: CONST_OVERRIDE.get(c, _band(ln)) for c, ln in consts.items()}

    edges, detail = defaultdict(set), defaultdict(list)
    for name, node in funcs.items():
        s = fmod[name]
        for sub in ast.walk(node):
            t = None
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                t = sub.func.id
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                t = sub.id
            if t is None:
                continue
            d = fmod.get(t) or cmod.get(t)
            if d and d != s:
                edges[s].add(d)
                detail[(s, d)].append("{}() references {}".format(name, t))
    counts = defaultdict(int)
    for n in funcs:
        counts[fmod[n]] += 1
    return edges, detail, dict(counts)


def verify_edges(present):
    """Edges that ACTUALLY exist, read from the target modules' own import statements.

    The facade is included as `__facade__`: it may import every layer (that is its job) and is
    exempt from the ordering rule, but a layer importing the facade would be a cycle and IS
    reported, because `__facade__` is not in ORDER and any layer naming it shows up here."""
    edges, detail = defaultdict(set), defaultdict(list)
    by_path = {v: k for k, v in MODULE_FILE.items()}
    files = [(by_path[p], p) for p in present]
    if os.path.exists(LIB):
        files.append(("__facade__", LIB))

    counts = {}
    for mod, path in files:
        tree = ast.parse(open(path, encoding="utf-8").read())
        if mod != "__facade__":
            counts[mod] = sum(1 for n in tree.body if isinstance(n, ast.FunctionDef))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for n in names:
                head = n.split(".")[0]
                if not head.startswith("reviewer_"):
                    continue
                target = head[len("reviewer_"):]
                if target == "lib" and mod != "__facade__":
                    edges[mod].add("__facade__")
                    detail[(mod, "__facade__")].append(
                        "line {}: a layer must never import the facade".format(node.lineno))
                elif target in ORDER and target != mod:
                    edges[mod].add(target)
                    detail[(mod, target)].append("line {}: import {}".format(node.lineno, head))
    return edges, detail, counts


def main():
    present = [p for p in MODULE_FILE.values() if os.path.exists(p)]
    if present:
        edges, detail, counts = verify_edges(present)
        mode = "VERIFY"
        print("MODE: VERIFY -- reading real imports of {} target module(s) + the facade".format(
            len(present)))
    else:
        edges, detail, counts = plan_edges()
        mode = "PLAN"
        print("MODE: PLAN -- no target module on disk; deriving edges from reviewer_lib.py by role")

    print("ORDER: " + " -> ".join(ORDER))
    for m in ORDER + ["__facade__"]:
        if edges.get(m):
            print("  {:<12} -> {}".format(m, ", ".join(sorted(edges[m]))))

    rank = {m: i for i, m in enumerate(ORDER)}
    back = sorted({(s, d) for s in edges for d in edges[s]
                   if s in rank and (d == "__facade__" or rank[d] > rank[s])})
    if counts:
        print("\nfunctions per module: " + ", ".join(
            "{}={}".format(m, counts[m]) for m in ORDER if m in counts)
            + " (total {})".format(sum(counts.values())))

    if back:
        print("\nFAIL [{}]: {} back-edge(s)".format(mode, len(back)))
        for s, d in back:
            print("  {} -> {}".format(s, d))
            for line in sorted(set(detail[(s, d)]))[:8]:
                print("      " + line)
        return 1
    print("\nPASS [{}]: acyclic, zero back-edges".format(mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
