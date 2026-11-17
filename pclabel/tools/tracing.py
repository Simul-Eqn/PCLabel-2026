"""What the curb and marking tracers share (no Qt, no GL): running a tracer's step generator to its end."""

from __future__ import annotations


def drive(steps, progress, stopped: dict) -> tuple[list, dict]:
    """Run a tracer's generator ``steps`` to its end: (the steps it yielded, its return value). ``progress(steps so
    far)`` may return False to stop early; the return value is then ``stopped``."""
    out = []
    try:
        while True:
            out.append(next(steps))
            if progress is not None and progress(out) is False:
                return out, stopped
    except StopIteration as e:
        return out, e.value
