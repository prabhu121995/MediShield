"""
Graph state for one case. Each agent writes exactly one key. `trace` is
append-only and `status` is last-writer-wins, so parallel branches
(kyc || claims) never clobber each other.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class TraceEvent(TypedDict):
    agent: str
    status: str          # ok | error
    started_at: str
    duration_ms: int
    note: str


def _last_wins(_old: str, new: str) -> str:
    return new


class CaseState(TypedDict, total=False):
    case_id: str
    file_path: str
    file_name: str
    received_at: str
    doc_type_override: str | None      # set by a human reviewer (HIL)

    preprocessor: dict[str, Any]
    classifier: dict[str, Any]
    kyc: dict[str, Any]
    claims: dict[str, Any]
    policy: dict[str, Any]
    fraud: dict[str, Any]
    orchestrator: dict[str, Any]

    trace: Annotated[list[TraceEvent], operator.add]
    status: Annotated[str, _last_wins]
