# SPDX-License-Identifier: MPL-2.0
import threading
import time

from reviewer_codex.cancellation import Cancellation


def test_completion_and_cancellation_are_linearized():
    token = Cancellation()
    entered = threading.Event()
    release = threading.Event()
    cancel_returned = threading.Event()

    def side_effect():
        entered.set()
        assert release.wait(1)
        return "landed"

    completed = []
    worker = threading.Thread(target=lambda: completed.append(token.complete(side_effect)))
    worker.start()
    assert entered.wait(1)
    canceller = threading.Thread(target=lambda: (token.cancel(), cancel_returned.set()))
    canceller.start()
    time.sleep(0.05)
    assert not cancel_returned.is_set()
    release.set()
    worker.join(1)
    canceller.join(1)

    assert completed == [(True, "landed")]
    assert not token.is_set()


def test_cancel_before_completion_prevents_side_effect():
    token = Cancellation()
    assert token.cancel()
    called = []

    assert token.complete(lambda: called.append(True)) == (False, None)
    assert called == []


def test_cancel_callbacks_run_before_cancel_returns():
    token = Cancellation()
    calls = []
    token.on_cancel(lambda: calls.append("bypass-written"))

    assert token.cancel()
    assert calls == ["bypass-written"]


def test_callback_registered_after_cancellation_runs_immediately():
    token = Cancellation()
    assert token.cancel()
    calls = []

    token.on_cancel(lambda: calls.append("late-bypass"))

    assert calls == ["late-bypass"]
