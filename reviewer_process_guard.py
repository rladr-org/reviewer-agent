#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""Bind a reviewer process to the lifetime of the hook that started it.

A hook can die in ways it cannot handle. Escape sends a signal the hook can catch, and
`reviewer_backends._run` does catch it -- but a SIGKILL, a host crash, or a terminal that goes
away leave no code of ours running at all, and the reviewer underneath is reparented to init
and keeps working (and spending) against a repository nobody is watching any more.

So the reviewer is not started directly. This guard is started instead, holding the read end of
a pipe whose write end only the hook keeps open. Whatever kills the hook closes that write end;
the read here returns EOF, and the guard terminates the reviewer and everything it started.
When the reviewer exits normally first, the guard simply forwards its exit code -- 128+N for a
signalled child, matching shell convention -- and the pipe never matters.

Executed as a script by `reviewer_backends._run`, never imported.
"""
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reviewer_base import terminate_process_tree  # noqa: E402


def _watch_parent(descriptor, completed, child_pid):
    try:
        while os.read(descriptor, 1):
            pass
    except OSError:
        pass
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if completed.is_set():
        return
    terminate_process_tree(child_pid, include_root=True)
    # _exit, not sys.exit: the child is gone and there is nothing left to flush, while a normal
    # exit would run the interpreter's shutdown from a non-main thread.
    os._exit(1)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        return 2
    try:
        lifecycle_descriptor = int(argv[0])
    except ValueError:
        return 2

    completed = threading.Event()
    try:
        child = subprocess.Popen(
            argv[1:],
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
            close_fds=True,
        )
    except OSError as error:
        print(str(error), file=sys.stderr)
        completed.set()
        try:
            os.close(lifecycle_descriptor)
        except OSError:
            pass
        return 127
    watcher = threading.Thread(
        target=_watch_parent,
        args=(lifecycle_descriptor, completed, child.pid),
        daemon=True,
    )
    watcher.start()
    return_code = child.wait()
    completed.set()
    if return_code < 0:
        return 128 - return_code
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
