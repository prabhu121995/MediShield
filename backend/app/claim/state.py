"""
Graph state for one claim: every document uploaded together, processed as one case.

Each agent writes exactly one key. `classified`, `docs` and `trace` are append-only, so each
page pass (classify, then extract) adds one page per step and the parallel branches (kyc || claims -> policy) never
overwrite each other's output.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


class DocumentIn(TypedDict, total=False):
    doc_id: str                 # the case_record id of this page
    file_path: str
    file_name: str
    doc_type_override: str | None


class IngestedDoc(TypedDict, total=False):
    doc_id: str
    file_name: str
    raw_text: str               # the transcription (vision model), read in the extract step
    image_quality: str          # clear | blurry | unreadable
    read_confidence: float
    blur_variance: float
    is_blurry: bool
    doc_type: str               # CLAIM_FORM | ID_DOCUMENT | ... | UNKNOWN
    classifier_confidence: float
    classifier_source: str      # vision | human_override
    classified_as: str          # what the model said, before the confidence threshold
    fields: dict[str, Any]      # identity and date fields read from the page
    guardrail_flags: list[dict] # input/output guardrail hits (claim/guardrails.py)
    error: str


class TraceEvent(TypedDict):
    agent: str
    status: str                 # ok | error
    started_at: str
    duration_ms: int
    note: str


def _last_wins(_old, new):
    return new


class ClaimState(TypedDict, total=False):
    claim_id: str
    title: str
    received_at: str
    documents: list[DocumentIn]                         # the queue, in upload order

    page: Annotated[dict[str, Any], _last_wins]         # unused; kept so older checkpoints still load
    classified: Annotated[list[IngestedDoc], operator.add]  # pass 1: one entry per classified page
    docs: Annotated[list[IngestedDoc], operator.add]    # pass 2: one entry per read (preprocessed + extracted) page
    plan: dict[str, Any]
    kyc: dict[str, Any]
    claims: dict[str, Any]
    duplicate: dict[str, Any]                           # the duplicate checkpoint: a repeat of a stored claim skips the agents
    policy: dict[str, Any]
    fraud: dict[str, Any]
    decision: dict[str, Any]
    hold: dict[str, Any]                                # the person's choice when page reading was incomplete
    review: dict[str, Any]                              # the human decision for an escalated claim

    trace: Annotated[list[TraceEvent], operator.add]
    status: Annotated[str, _last_wins]
