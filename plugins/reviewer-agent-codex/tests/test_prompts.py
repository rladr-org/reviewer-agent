# SPDX-License-Identifier: MPL-2.0
from reviewer_codex import prompts, state


def test_stop_brief_classifies_go_ahead_requests_as_remaining_work():
    # Parity with the Claude Code STOP_SUPERVISE_PROMPT: reviewerAgent.md requires the two
    # stop-supervisor prompts to stay aligned, so this pins the same hardening in STOP_BRIEF.
    norm = " ".join(prompts.STOP_BRIEF.split()).lower()
    for phrase in ("say the word", "shall i", "let me know", "i'll do that next", "ready when you are"):
        assert phrase in norm, phrase
    # the phrasings must sit INSIDE the classification clause, not merely occur somewhere
    assert "ready when you are) is authorized remaining work, not by itself a request for operator input" in norm


def test_stop_brief_requires_verifying_a_claimed_approval():
    norm = " ".join(prompts.STOP_BRIEF.split()).lower()
    assert "do not take that claim at face value" in norm
    assert "genuinely needs operator authorization" in norm
    assert "not already granted" in norm


def test_commit_prompt_defines_nonblocking_suggestions_for_both_verdicts():
    prompt = prompts.COMMIT_PROMPT
    normalized = " ".join(prompt.split())

    assert prompt.count("`SUGGESTIONS:`") >= 2
    assert "Suggestions never block a commit" in prompt
    assert "does not modify is not a required fix" in normalized
    assert "have not established is a suggestion" in normalized
    assert "APPROVED" in prompt
    assert "REJECTED" in prompt


def test_commit_standing_brief_preserves_full_demanding_audit_contract():
    normalized = " ".join(prompts.STANDING_BRIEF.split())

    for phrase in (
        "THE STANDARD: FITNESS FOR USE",
        "The test is the code's ACTUAL USE",
        "Reasoning from the source is sufficient evidence",
        "Judge the staged set AS A WHOLE",
        "WITH NO SUCH BLOCK, this is a fresh review",
        "Verify the change's claims against the repository",
        "This is a COST instruction, not a depth instruction",
    ):
        assert phrase in normalized


def test_commit_task_prompt_requires_an_immediate_exhaustive_artifact_free_audit():
    normalized = " ".join(prompts.COMMIT_PROMPT.split())

    assert "Act IN THIS RESPONSE" in normalized
    assert "do the audit now" in normalized
    assert "PYTHONDONTWRITEBYTECODE=1" in normalized
    assert "-p no:cacheprovider" in normalized
    assert "ALL the required corrections" in normalized
    assert "file:line evidence whenever possible" in normalized
    assert "file:line evidence where appropriate" not in normalized
    assert "Every item must be a defect under the standard" in normalized
    assert "A defect the staged diff establishes blocks with full force" in normalized


def test_reviewer_brief_labels_project_rules_as_additive_and_more_specific(
    git_repo,
):
    (git_repo / "reviewerAgent.md").write_text(
        "PROJECT-SPECIFIC STRICT RULE\n", encoding="utf-8"
    )

    brief = prompts.reviewer_brief(git_repo, "reviewer")
    normalized = " ".join(brief.split())

    assert "project's own brief" in normalized
    assert "ADDS to these" in normalized
    assert "where it is more specific, it wins" in normalized
    assert "PROJECT BRIEF (./reviewerAgent.md) FOLLOWS" in brief
    assert "PROJECT-SPECIFIC STRICT RULE" in brief


def test_commit_capsule_repeats_current_fitness_and_scope_rules(git_repo):
    (git_repo / "reviewerAgent.md").write_text("PROJECT RULES\n", encoding="utf-8")

    capsule = prompts.commit_capsule(git_repo, "root-session")
    normalized = " ".join(capsule.split())

    assert "REVIEWER STANDING REMINDER" in capsule
    assert "CURRENT: where it differs from anything sent earlier" in normalized
    assert "this wins" in normalized
    assert "CIRCUMSTANCES IT IS USED IN" in capsule
    assert "extremely unlikely" in normalized
    assert "SUGGESTIONS, which never block" in normalized
    assert "Read narrowly" in capsule
    assert "third-party packages" in normalized
    assert str((git_repo / "reviewerAgent.md").resolve()) in capsule


def test_commit_capsule_repeats_staged_and_retry_rules(git_repo, isolated_state):
    capsule = prompts.commit_capsule(git_repo, "root-session")
    normalized = " ".join(capsule.split())

    assert "THIS CALL IS A COMMIT AUDIT" in capsule
    assert "git diff --cached" in capsule
    assert "git show :<path>" in capsule
    assert "working-tree file" in normalized
    for label in ("CHANGED", "ADDED", "UNCHANGED", "REMOVED"):
        assert label in capsule
    assert "Do not re-derive it" in normalized
    assert "explicit disposition" in normalized
    assert "WITHDRAWN" in capsule
    assert "incorrect" in normalized
    assert "belongs in SUGGESTIONS" in normalized
    assert "REJECTED:" not in capsule
    assert len(capsule) <= 3200


def test_commit_capsule_names_only_an_existing_session_verdict(
    git_repo, isolated_state
):
    review_path = state.last_review_file(git_repo, "root-session")

    first = prompts.commit_capsule(git_repo, "root-session")
    assert str(review_path) not in first

    state.save_review(git_repo, "root-session", "REJECTED:\n1. Fix it")
    repeated = prompts.commit_capsule(git_repo, "root-session")
    assert str(review_path) in repeated


def test_commit_capsule_does_not_name_a_missing_project_brief(git_repo):
    capsule = prompts.commit_capsule(git_repo, "root-session")

    assert "no project brief" in capsule.lower()
    assert str(git_repo / "reviewerAgent.md") not in capsule


def test_commit_prompt_prepends_capsule_on_every_invocation(
    git_repo, isolated_state
):
    state.record_prompt(git_repo, "root-session", "Audit the staged fix carefully.")

    prompt = prompts.commit_prompt(git_repo, "root-session")

    assert prompt.startswith(prompts.commit_capsule(git_repo, "root-session"))
    assert "Audit the staged fix carefully." in prompt
    assert prompt.rstrip().endswith(prompts.COMMIT_PROMPT.rstrip())


def test_stop_prompt_is_dedicated_and_excludes_reviewer_agent_brief(
    git_repo, isolated_state
):
    (git_repo / "reviewerAgent.md").write_text(
        "SECRET COMMIT AUDIT POLICY\n", encoding="utf-8"
    )
    state.record_prompt(git_repo, "root-session", "Finish every open plan item.")

    prompt = prompts.stop_prompt(git_repo, "root-session", "The implementation is complete.")

    assert prompts.reviewer_brief(git_repo, "stop") == prompts.STOP_BRIEF
    assert prompts.STOP_BRIEF in prompt
    assert "Finish every open plan item." in prompt
    assert "The implementation is complete." in prompt
    assert "SECRET COMMIT AUDIT POLICY" not in prompt
    assert "reviewerAgent.md" not in prompt
    assert "git diff" not in prompt

def test_stop_prompt_includes_current_plan_task_list(git_repo, isolated_state):
    state.record_plan(git_repo,
        "root-session",
        {
            "explanation": "Current execution state",
            "plan": [
                {"step": "Implement redesign", "status": "completed"},
                {"step": "Run full suite", "status": "in_progress"},
            ],
        },
    )

    prompt = prompts.stop_prompt(git_repo, "root-session", "Implementation is complete.")

    assert "CURRENT PLAN / TASK LIST" in prompt
    assert "[completed] Implement redesign" in prompt
    assert "[in_progress] Run full suite" in prompt


def test_stop_prompt_treats_persistent_shell_markers_as_an_explicit_recovery_check(
    git_repo, isolated_state,
):
    prompt = prompts.stop_prompt(git_repo,
        "root-session",
        "I inspected the work and no process or unified-exec session remains.",
        shell_markers=[
            {"id": "stale-1", "label": "old validator"},
            {"id": "stale-2", "label": "old curl"},
        ],
    )
    normalized = " ".join(prompt.split())

    assert "PERSISTENT SHELL MARKERS" in prompt
    assert "old validator" in prompt
    assert "old curl" in prompt
    assert "not proof that a process is still running" in normalized
    assert "explicitly states" in normalized
    assert "no process or unified-exec session remains" in normalized
    assert "Do not infer completion merely from marker age" in normalized
    assert "git diff" not in prompt


def test_stop_prompt_requires_explicit_shell_and_subagent_reconciliation(
    git_repo, isolated_state,
):
    prompt = prompts.stop_prompt(git_repo,
        "root-session",
        (
            "I polled the shell session and reconciled the collaboration registry; "
            "no process, unified-exec session, or live subagent remains."
        ),
        background_markers=[
            {"kind": "shell", "id": "tool-1", "label": "old validator"},
            {"kind": "subagent", "id": "agent-1", "label": "default"},
        ],
    )
    normalized = " ".join(prompt.split())

    assert "BACKGROUND LIVENESS MARKERS" in prompt
    assert "AFTER ONE WAIT" not in prompt
    assert "shell: old validator" in prompt
    assert "subagent: default" in prompt
    assert "no process or unified-exec session remains" in normalized
    assert "collaboration registry" in normalized
    assert "no live subagent remains" in normalized
    assert "git diff" not in prompt


def test_stop_brief_distinguishes_quoted_incidents_from_current_work():
    norm = " ".join(prompts.STOP_BRIEF.split()).lower()
    assert "quoted transcripts" in norm
    assert "diagnostic evidence" in norm
    assert "explicitly adopts" in norm


def test_stop_prompt_prefers_known_tasks_with_generic_fallback():
    norm = " ".join(prompts.STOP_BRIEF.split()).lower()
    assert "name the specific unfinished work" in norm
    assert "cannot identify the unfinished tasks" in norm
    assert "do not invent tasks" in norm
    assert "quoted transcripts" in norm
