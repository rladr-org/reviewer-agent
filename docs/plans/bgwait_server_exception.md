# A background server is not proof of a background wait

## Problem

`has_outstanding_background_task` lets a stop through with no supervisor call while a
`run_in_background` shell is still running, because Claude Code re-invokes the agent when that
shell exits. A server never exits. In one operator session a local review web server
(`conda run ... python .../serve.py`, `run_in_background`) was restarted several times; each
restart muted stop supervision for up to the 12-hour shell window (`bgShellStaleSecs`). Over
three days 42 stops in that project were allowed this way (the supervisor itself approved 29),
including stops that only asked the operator questions they had already answered.

Every background shell wakes the agent on exit, so "would wake the agent" cannot separate a test
run from a server. Turn timing cannot either: a long GPU run legitimately outlives several turns
of conversation, and handing those stops to the supervisor would nudge an agent that is really
waiting.

## Design

A listening TCP port is strong evidence of a server, but not proof that nobody is waiting: a GPU
job may run a dashboard, and an integration test may start a local server. So a listening task
does not decide the stop either way; it sends the stop to the supervisor with the facts.

- Detection. The launch acknowledgement names the task id and its output file:
  `Command running in background with ID: <id>. Output is being written to: <path>.output.`
  Only an absolute path ending in `/<id>.output` is accepted. `lsof -F pn -- <files>` gives the
  processes holding each output file (the shell and every child that inherited its stdout); their
  descendants come from `ps -A -o pid= -o ppid=`, so a server that redirected its own output is
  still found through the wrapper shell. `lsof -a -iTCP -sTCP:LISTEN -F p -p <pids>` gives the
  listening ones. A task with any listening process is "listening".
- Decision. Background work that would suppress the stop today keeps suppressing it only if at
  least one outstanding task is NOT listening (the job-state `fan` list, shells launched in an
  earlier turn, current-turn launches, monitors, async sub-agents all count as before). If every
  outstanding background task is a listening shell, the stop is reviewed.
- Supervisor context. For that review the stop hook adds a block naming each listening
  background task (its description or command, start time, and that it is still running and
  listening on a port). The shared stop prompt gets one rule: a still-running background task the
  agent is genuinely waiting for, with nothing else it can do meanwhile, is a reason to reply
  STOP_OK; a running server the agent merely started for the operator to use is not, and the stop
  is judged on everything else as usual.
- Failure. The probe runs only when every outstanding task is a shell, with a short timeout per
  command. Any failure (no `lsof`, timeout, unexpected output) classifies nothing as listening,
  which is today's behavior: the stop is treated as a background wait.

So a misclassified GPU job costs one supervisor call that should approve, never a muted review,
and a server the probe can see no longer silences supervision. Where the probe cannot run (no
`lsof`, as on some minimal Linux images, or a timeout), a server still silences it as it does
today; that host-dependent gap is accepted rather than nudging every real wait there.

## Not changed

Monitors and async sub-agents, the staleness windows, and the early return in `stop_hook.py`
for real waits. A question asked while a real, non-listening test run is going is still let
through; the next stop after the run finishes is reviewed.

## Tests

- Decision, with the probe stubbed: a listening shell alone (launched in an earlier turn, in the
  current turn, or listed in the job state) does not suppress; a listening shell beside a
  non-listening shell, a monitor or an async sub-agent still does.
- Supervisor context: the stop review for a listening-only wait carries the block naming the task,
  and the shared stop prompt carries the new rule.
- The probe against real processes (skipped without `lsof`): a Python HTTP server writing to a
  `tasks/<id>.output` file is listening; a `sleep` writing to another is not; a server whose
  wrapper shell holds the file is found through the process tree.
- Fail-open: with no `lsof`, a listening shell still suppresses (today's behavior).
