# Privacy

What leaves your machine, what stays on it, and how to remove what stays.

This project operates no service. There is no account, no server, no telemetry endpoint, and
nothing is sent anywhere except through the AI provider you have already configured and
authenticated yourself.

## What is sent to a provider

Every reviewer call goes to the backend you selected — the Codex CLI (OpenAI), Pi (whichever
model you have configured there), or the Claude CLI (Anthropic) — under your own credentials,
subject to that provider's terms and data policy. This project adds no destination of its own,
and holds no credential of yours: each CLI authenticates itself exactly as it does when you run
it by hand.

On the `claude` backend that has a billing consequence, not just a privacy one. The reviewer is a
second `claude` process running on **your own Claude subscription**, so its tokens come out of the
same allowance as your coding session. An exhausted allowance blocks commits until it resets,
because the commit gate is fail-closed.

What a call contains depends on which call it is:

| Call | Contents |
|---|---|
| Session brief (once per project, session and role) | The project's `reviewerAgent.md`, plus this package's standing reviewer instructions. |
| Commit audit | The staged diff (`git diff --cached`), your typed messages from the session transcript, any `/reviewerDirective` standing directives, the list of paths that changed since the reviewer's previous audit, and — on the Codex backend — whatever the reviewer then chooses to read or run in the repository. On the Pi and Claude backends the reviewer additionally reads repository files for context, but runs nothing. |
| Timed-out `AskUserQuestion` answer (Claude Code only) | The question and its options, your typed messages, and directives. No staged diff. |
| Stop supervision | The agent's proposed final message, your typed messages, directives, and — on native Codex — the current plan and task list. Deliberately not the commit briefing. |

Two consequences worth stating plainly. First, on every backend the reviewer reads more than the
diff, because judging a change often requires reading the code around it — and on the Codex
backend it also runs commands there. Second, your own words to your coding agent are relayed to
the reviewer, because a reviewer that cannot see "yes, I know this is a temporary hack" rejects
things you already authorized.

## What stays on your machine

Nothing here is uploaded by this project. All of it is plaintext.

**Claude Code package** — under `~/.claude/reviewer-agent/` (override with
`REVIEWER_STATE_DIR`):

| Path | Contents |
|---|---|
| `state/` | Per-project on/off flags, backend selection, standing directives, reviewer and stop-supervisor session ids (one slot per backend and role), operator-message watermarks, nudge counters, the audited-stage record, and the brief a session actually received. |
| `reviews/` | The latest verdict per project and session, plus a `.history/` directory of retained verdicts and outcome records. Verdicts quote the diffs they judged. |
| `logs/reviewer.log` | One line per gated action: project, backend, session id, exit code, verdict, timing and token counts, plus bounded tails of the verdict and of stderr. |
| `config.json` | Your settings, if you created one. |

**Codex package** — under `~/.codex/reviewer-agent-codex/` (override with
`REVIEWER_CODEX_STATE_DIR`): the same shape — `sessions/`, `prompts/`, `plans/`, `nudges/`,
`liveness/`, `commit-tickets/`, `stop-permits/`, `commit-cursors/`, `reviews/` and `config.json`.
The `prompts/` ledger holds your recent typed prompts, per project and session, so a stop
supervisor can see what you asked for.

Provider-side conversation history is separate and lives wherever that provider keeps it. This
project stores reviewer *thread identifiers* so it can resume a conversation; the conversation
itself belongs to the provider.

## Retention and removal

Retained verdicts are capped per project: the newest **100** history entries by default, pruned
oldest-first. Verdicts and non-verdict outcome records (timeouts, cancellations, failures) share
that one cap. Change it with `reviewHistoryKeep` in the package's `config.json`, or with
`REVIEWER_REVIEW_HISTORY_KEEP` (Claude Code) / `REVIEWER_CODEX_REVIEW_HISTORY_KEEP` (Codex).

To purge one project's review record without uninstalling anything:

```bash
/reviewerHistory clear                              # Claude Code
$reviewer-agent-codex:reviewer-history clear        # Codex
```

Both delete that project's retained history **and** its saved latest rulings, and touch no other
repository. `/reviewerHistory` and `...reviewer-history status` report what is retained and
where, first.

To remove everything for every project, delete the state directory:

```bash
rm -rf ~/.claude/reviewer-agent           # Claude Code package
rm -rf ~/.codex/reviewer-agent-codex      # Codex package
```

That also removes reviewer session ids, so the next gated action briefs a fresh reviewer. See
the uninstall sections of the READMEs for removing the plugins themselves.

## Telemetry

None. This project collects no usage data, sends no crash reports, and contacts no endpoint of
its own. If that ever changes it will be announced in `CHANGELOG.md`, documented here, and
opt-in.

The AI provider you selected has its own telemetry and retention behaviour, which this project
neither controls nor alters.
