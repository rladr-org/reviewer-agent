# Security

> **No security properties are claimed.** This document tells you how to report a vulnerability
> privately, and warns you what the software does. It does not assert that anything is safe,
> sandboxed, bounded, or correctly isolated. Nothing here has been formally verified or audited,
> and nothing in it may be relied upon. Use of this software is entirely at your own risk — see
> [`DISCLAIMER.md`](DISCLAIMER.md) and [`LICENSE`](LICENSE).

## Reporting a vulnerability

Report privately through GitHub's private vulnerability reporting on this repository:
**Security → Report a vulnerability**. That delivers the report to the repository owner's GitHub
notifications and keeps it out of public view.

Use it rather than a public issue. This software runs inside a developer environment, so a public
report may expose details that put users at additional risk.

> **No response is promised.** This is a personal project published as-is. There is no
> undertaking that reports are read, acknowledged, triaged, answered, or acted upon; that any
> vulnerability will ever be fixed, mitigated, or disclosed; or that any patch will be published.
> A report may be ignored or closed without a reply, and the project may be archived or abandoned
> at any time without notice. Decide your own disclosure timing on the assumption that no fix is
> coming.

## What you are running

Stated as a warning, not a boundary you can rely on:

- It inserts a **second AI agent** between the developer agent and each commit, and gives the
  reviewer the staged changes and files from the repository.
- On the **Codex backend the reviewer executes commands in the repository**, including the
  repository's test suite. Treat enabling that backend as "arbitrary commands may run here
  automatically when the developer agent attempts a commit".
- It **sends your code to a third-party AI provider** under your own credentials.
- **Repository content is attacker-controlled input.** Any branch, dependency, or patch you did
  not write becomes reviewer input, and a crafted one is a prompt-injection vector against the
  reviewer — and, on the Codex backend, an influence on which commands it chooses to run.
- Its verdicts are **model output**. It can approve defective or insecure code and miss
  vulnerabilities entirely. It is not an audit, a scanner, or a compliance control.
- `reviewerAgent` and `stopContinue` may return answers, permissions or imperative directions that
  another agent acts on using that agent's own tools and credentials. The supervisor may
  misunderstand the user's scope or incorrectly decide that an action was already authorized.
- `/reviewerDirective` is handled by a `UserPromptSubmit` hook that reads its text from JSON, not a
  shell, so shell syntax in a directive cannot execute. Its content is still injected into every
  reviewer prompt, so pasting untrusted text remains a prompt-injection vector against the reviewer.
  The other bundled Claude Code slash commands use `!` shell preprocessing (through one dispatcher)
  and pass their argument text through the shell; they are manual-only and take constrained tokens,
  but users must not paste untrusted text or shell syntax into them.

The design intends the commit gate to fail closed and the question and stop paths to fail open,
and intends the Pi and Claude backends to have no shell. Those are intentions, and the source is
public: **verify anything you plan to depend on, rather than taking this document's word for it.**
