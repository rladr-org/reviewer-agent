# SPDX-License-Identifier: MPL-2.0
"""Linearizable cancellation for MCP requests with irreversible side effects."""

import threading


class Cancellation:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._completed = False
        self._callbacks = []

    def cancel(self):
        with self._lock:
            if self._completed or self._event.is_set():
                return False
            self._event.set()
            callbacks = self._callbacks
            self._callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # Cancellation must still reach the caller if cleanup fails.
                pass
        return True

    def on_cancel(self, callback):
        """Run `callback` synchronously when cancellation wins."""
        with self._lock:
            if self._completed:
                return False
            if not self._event.is_set():
                self._callbacks.append(callback)
                return True
        try:
            callback()
        except Exception:
            # Match cancel(): a cleanup failure cannot undo cancellation.
            pass
        return True

    def set(self):
        return self.cancel()

    def is_set(self):
        return self._event.is_set()

    def wait(self, timeout=None):
        return self._event.wait(timeout)

    def complete(self, action=None):
        """Linearization point: cancellation wins, or `action` runs and completion wins."""
        with self._lock:
            if self._event.is_set():
                return False, None
            if self._completed:
                return True, None
            result = action() if action is not None else None
            self._completed = True
            self._callbacks = []
            return True, result


def complete(token, action):
    """Run an irreversible action only if production cancellation has not won."""
    if token is None:
        return True, action()
    method = getattr(token, "complete", None)
    if method is not None:
        return method(action)
    if token.is_set():
        return False, None
    return True, action()
