# Privacy

> **This is a description, not a guarantee.** It sets out how the software is built, to the
> author's understanding, at the time of writing. It has not been audited, it may be incomplete
> or wrong, and a later version may behave differently. Nothing here may be relied upon — see
> [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE). If a data-handling property matters
> to you, verify it against the source rather than against this page.

## Your code goes to an AI provider

That is the whole point of the tool, and it is the fact worth being sure you understand before
installing it.

Reviewer calls use the backend you select — the Codex CLI (OpenAI), Pi and its configured model, or
the Claude CLI (Anthropic) — subject to the provider's terms, retention and data handling. The
current project code does not operate an author-controlled service or intentionally collect a
provider credential; the invoked tools perform their own authentication and may keep credentials,
state and telemetry under their own rules.

What may be sent depends on the call and backend:

| Call | Content that may be provided or read |
|---|---|
| Session brief | The project's `reviewerAgent.md` and package-owned reviewer instructions. |
| Commit audit | The staged diff, typed session messages, standing directives, retry context and repository files the reviewer reads. A Codex reviewer may also run repository commands. |
| Stop supervision | The proposed final message (for a rerouted `AskUserQuestion`, this includes the question and its options), typed operator messages, directives and available plan, task or liveness context. |

The exact content can change with the selected host, backend and provider version. Inspect the
source and provider settings if a particular data boundary matters.

What the provider then does with it is between you and them. Read their terms.

## What it writes on your machine

Under `~/.claude/reviewer-agent/` (override with `REVIEWER_STATE_DIR`), and
`~/.codex/reviewer-agent-codex/` for the Codex package (`REVIEWER_CODEX_STATE_DIR`):

- **`state/`** — per-project flags, backend selection, directives, reviewer session ids,
  watermarks, counters, and risk-acceptance receipts. A receipt contains the repository identity,
  disclaimer SHA-256 hash, acceptance source, and local Unix timestamp; it is shared by both
  features within that host and repository.
- **`reviews/`** — the latest verdict per project and session, plus a bounded history. **Verdicts
  quote the diffs they judged**, so this directory contains excerpts of your source code.
- **`logs/reviewer.log`** — one line per gated action, including bounded tails of the verdict and
  of stderr.
- **`config.json`** — your settings, if you made one.

The current project code stores these files without application-level encryption or obfuscation.
Anything with access to the relevant state directory may be able to read them. The project is not
designed to upload the state directories as a separate operation, but their contents or equivalent
context may be sent during reviewer calls, and invoked tools have their own data handling.

## Removing it

Purge one project's retained reviews and rulings without uninstalling anything:

```bash
/reviewerHistory clear                              # Claude Code
$reviewer-agent-codex:reviewer-history clear        # Codex
```

Remove everything, for every project:

```bash
rm -rf ~/.claude/reviewer-agent           # Claude Code package
rm -rf ~/.codex/reviewer-agent-codex      # Codex package
```

Reviewer conversations held by the provider are separate and live wherever that provider keeps
them; this project stores only the thread identifiers needed to resume them.

Disabling either feature leaves its risk-acceptance receipt in place. Deleting the applicable
state directory removes the receipt along with all other local state, and a later enablement then
requires a new explicit acceptance.

## Telemetry

To the author's current understanding, the project code has no author-operated telemetry endpoint
and does not intentionally send project-specific usage or crash reports to the author. The invoked
CLIs, model providers, plugin hosts and distribution platforms have their own telemetry, logging and
retention behavior, which this project does not control. Treat this as a description of the current
source, not a promise about another product or a future version.
