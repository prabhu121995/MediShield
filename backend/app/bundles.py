"""
Case bundles: one claim case that holds several documents uploaded together.

Each document is still processed on its own by the pipeline. This module only rolls the
document results up into one case status, using the same precedence as the per-document
orchestrator: ESCALATE > REJECT > APPROVE. A document that failed, or is still being
read, cannot be cleared, so it keeps the case open.
"""

from __future__ import annotations

from typing import Any


def summarize_bundle(documents: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(documents)
    if total == 0:
        return {"status": "EMPTY", "decision": None, "documents": 0, "processing": 0,
                "failed": 0, "needs_review": 0}

    processing = sum(1 for d in documents if d.get("status") == "PROCESSING")
    failed = sum(1 for d in documents if d.get("status") == "FAILED")
    needs_review = sum(1 for d in documents if d.get("needs_review") or d.get("decision") == "ESCALATE")
    decisions = {d.get("decision") for d in documents if d.get("decision")}

    if processing:
        status, decision = "PROCESSING", None
    elif failed:
        status, decision = "FAILED", "ESCALATE"
    elif "ESCALATE" in decisions or needs_review:
        status, decision = "NEEDS_REVIEW", "ESCALATE"
    elif "REJECT" in decisions:
        status, decision = "REJECTED", "REJECT"
    else:
        status, decision = "APPROVED", "APPROVE"

    return {"status": status, "decision": decision, "documents": total, "processing": processing,
            "failed": failed, "needs_review": needs_review}
