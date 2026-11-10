# Give completion reviews the current turn's assistant messages

## Evidence and scope

The repeat completion test approved an intentionally incomplete draft despite a
visible promise to continue. The reviewer had the repeat request and plan, but
not the assistant's intervening commentary. This establishes a missing evidence
source, not proof of the model's internal reason for its wrong ruling.

A read-only probe of the installed app-server returned this live turn's actual
agentMessage items with phase=commentary. The documented interfaces are
thread/read and thread/turns/list (itemsView=full). Neither resumes the thread or
starts a model turn. Reference: https://learn.chatgpt.com/docs/app-server.

## Implementation contract

- At completion review, read a bounded snapshot of the host-bound current turn
  through a short-lived local app-server client. Use only initialize, initialized,
  thread/read and thread/turns/list. Do not parse rollout files, start/resume turns,
  mutate conversations, subscribe to events or request a model response.
- Validate thread identity, repository identity and exact turn identity before
  using messages. Never infer identity from an agent-supplied draft or tool input.
  Read at most one small newest-turn page; missing/unsupported data is explicitly
  unavailable, not proof that the agent made no promises. No unbounded fallback.
- Include only user-visible user and assistant text from the matching turn, in
  order, labelled by role, phase, turn and item identity. Exclude reasoning, tool
  arguments/results and unrelated turns. Bound bytes, message count and text;
  disclose truncation. Assistant statements are evidence of commitments and
  completion, never operator authority. A repeated request is new work even if
  a previous run was complete; evaluate the current turn separately.
- Supply the same evidence to draft review, native Stop and fresh reassessment.
  Use host cancellation and a short total timeout, and terminate/reap the helper
  on every outcome. Missing data must not grant approval or defeat existing
  fail-open review behavior. The snapshot does not show future messages or prove
  that desktop painting worked.
- Keep unit tests hermetic: existing review fixtures stub this new read-only
  boundary; dedicated transport tests use an isolated fake app-server. Verify
  the installed host separately through the real read-only API.

## Delivery and checks

Commit this plan, then failing tests, then implementation/docs. Tests must prove
that a real commentary item reaches the prompt sent to the reviewer, with role
and current-turn boundaries; malformed identity, other repositories/turns,
reasoning and tool contents must not leak. Exercise unavailable endpoints,
oversized output, cancellation, timeout and child cleanup through the transport.
Run a live read of this conversation and a real reviewer case combining an older
completed run, a new repeat request, an assistant promise and an incomplete draft;
require rejection followed by corrected approval, with question-only and completed
promise controls. Passing prompt substring checks alone does not prove judgment.

Run both suites and affected export/privacy/version checks. Install the reviewed
build while preserving settings and active sessions. No push or public release.
If this host cannot expose commentary reliably, report the limitation rather than
substituting a claim that commentary was captured. The user still observes GUI
visibility because computer-use access to Codex was denied.
