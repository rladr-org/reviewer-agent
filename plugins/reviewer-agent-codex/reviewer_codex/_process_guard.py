# SPDX-License-Identifier: MPL-2.0
"""Keep a nested reviewer bound to the lifetime of its MCP parent."""

import os
import subprocess
import sys
import threading

try:
    from .process_tree import terminate_process_tree
except ImportError:  # Executed directly by backend._run.
    from process_tree import terminate_process_tree


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
