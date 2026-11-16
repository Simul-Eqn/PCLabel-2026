"""Running work off the UI thread, with its result handled in the UI thread.

A worker's result must reach its handler in the UI thread: a handler that once ran in the worker thread hung the
import (it waited for its own thread). ``run_in_worker`` does this for every panel.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal


class _Relay(QObject):
    done = Signal(object, object, float)  # result, error, seconds


_live: set = set()  # relays waiting for their worker (kept alive until it is done)


def run_in_worker(fn, on_done, name: str = "worker") -> threading.Thread:
    """Run ``fn()`` in a daemon thread; ``on_done(result, error, seconds)`` then runs in the UI thread, with the
    exception ``fn`` raised (result None) or its result (error None). Call it from the UI thread."""
    relay = _Relay()  # lives in the calling (UI) thread, so its signal is delivered there

    def finish(res, err, seconds):
        _live.discard(relay)
        on_done(res, err, seconds)

    relay.done.connect(finish)
    _live.add(relay)

    def work():
        t0 = time.perf_counter()
        try:
            res = fn()
        except Exception as e:  # reported in the UI thread
            relay.done.emit(None, e, time.perf_counter() - t0)
            return
        relay.done.emit(res, None, time.perf_counter() - t0)

    t = threading.Thread(target=work, daemon=True, name=name)
    t.start()
    return t
