"""
Shared plumbing for every agent node: timing, a trace event, and error capture.
A failing agent writes {"error": ...} as its output instead of crashing the case.
"""

from __future__ import annotations

import functools
import time
from datetime import datetime, timezone
from typing import Any, Callable

from app.state import CaseState, TraceEvent


def node(agent_name: str, writes: str) -> Callable:
    def decorator(fn: Callable[[CaseState], dict[str, Any]]) -> Callable:
        @functools.wraps(fn)
        def wrapper(state: CaseState) -> dict[str, Any]:
            started = datetime.now(timezone.utc).isoformat()
            t0 = time.perf_counter()
            try:
                output = fn(state) or {}
                status, note = "ok", ""
            except Exception as exc:              # one bad agent must not kill the case
                output = {"error": f"{type(exc).__name__}: {exc}"}
                status, note = "error", str(exc)[:200]

            event: TraceEvent = {
                "agent": agent_name,
                "status": status,
                "started_at": started,
                "duration_ms": int((time.perf_counter() - t0) * 1000),
                "note": note,
            }
            return {writes: output, "trace": [event], "status": agent_name.upper()}

        return wrapper
    return decorator
