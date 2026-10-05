# Codex approval verdict and explanatory prose

The recipes reviewer returned APPROVED followed by dispositions of prior findings.
One disposition said that an unrelated-cell input "is rejected with a cell mismatch".
The Codex commit parser searches the entire pre-suggestions response for REJECTED,
so this correct explanation incorrectly vetoed the leading approval.

Parse the first nonempty line as the verdict. Keep refusing a first-line approval
that also contains a rejection token, and refuse a separate explicit REJECTED
verdict line or heading. Do not infer a rejection verdict from the word appearing
in explanatory sentences or prior-finding dispositions. Retain the existing
inspection-failure guard, malformed-suggestions checks, and fail-closed handling
of missing/invalid approval tokens. Preserve the full reviewer response in results
and history. This change is Codex-only.

Protocol: commit this plan, add and run failing regressions, commit those tests,
then implement and run the full Codex suite before committing. Regressions include
the exact recipes ruling, prose before SUGGESTIONS, a real temporary Git commit
using that ruling, and contradictory/invalid/inspection-failure controls. Verify
the installed copy after integration. Keep all existing release edits untouched;
refresh the installation build separately if those edits remain pending.
