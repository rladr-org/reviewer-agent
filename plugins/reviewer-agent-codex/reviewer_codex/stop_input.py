# SPDX-License-Identifier: MPL-2.0
"""Cancel native stop work when the prompt hook records a newer operator message."""

import threading
from contextlib import contextmanager

from . import state
from .cancellation import Cancellation


@contextmanager
def watch(root, ticket, cancel_event, enabled):
    finished = threading.Event()
    # Submission cancels reviewer work, not the host's outstanding MCP request: it
    # still needs an empty hook result. Escape cancels both through the parent token.
    review_cancel = Cancellation() if enabled else cancel_event
    if enabled and cancel_event is not None:
        register = getattr(cancel_event, "on_cancel", None)
        if register is not None:
            register(review_cancel.set)
        if cancel_event.is_set():
            review_cancel.set()

    def submitted():
        if not enabled:
            return False
        changed = state.stop_generation(root, ticket["session_id"], strict_io=True) != ticket["operator_generation"]
        if changed:
            review_cancel.set()
        return changed

    def monitor():
        while not finished.wait(0.1):
            try:
                if cancel_event is not None and cancel_event.is_set():
                    review_cancel.set()
                    return
                if submitted():
                    return
            except OSError:
                # A failed input check must not leave an unattended reviewer running.
                review_cancel.set()
                return

    worker = None
    if enabled:
        worker = threading.Thread(target=monitor, daemon=True)
        worker.start()
    try:
        yield review_cancel, submitted
    finally:
        finished.set()
        if worker is not None:
            worker.join()
