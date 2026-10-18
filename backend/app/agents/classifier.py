"""
Stage 2 -- text to doc_type, from content markers only (no LLM, no filename).

Title phrases weigh 3, generic fields weigh 1, so identity boilerplate that every
form shares cannot outvote the title. On a tie, the earlier title wins at a
lower confidence. Markers were tuned on the MediShield dataset (see WORKFLOW.md).
A reviewer can override the type (HIL); the override is used as-is.
"""

from __future__ import annotations

import re

from app import config
from app.agents._base import node
from app.state import CaseState

MARKERS: dict[str, list[tuple[str, int]]] = {
    "CLAIM_FORM": [
        (r"CMS-?1500|UB-?04|CLAIM\s*FORM", 3),
        (r"PLACE OF SERVICE|TOTAL\s*CHARGE|PROVIDER\s*NPI|\bCPT\b", 1),
    ],
    "ID_DOCUMENT": [
        (r"DRIVER|PASSPORT|IDENTIFICATION|STATE\s*ID|MEDICARE|MEMBER\s*CARD", 3),
        (r"DATE\s*OF\s*BIRTH|\bEXP(IRY|IRES|IRATION)?\b|\bCARD\b|MEMBER\s*I\b|GROUP\s*(NUMBER|NO\.?|#|:)|ENTITLED", 1),
    ],
    "DISCHARGE_SUMMARY": [
        # "PATIENT DISCHARGE" alone: one layout prints the address between it and "REPORT".
        (r"DISCHARGE\s*(SUMMARY|REPORT)|PATIENT\s*DISCHARGE", 3),
        (r"ADMISSION\s*DATE|DISCHARGE\s*DATE|HOSPITAL\s*COURSE", 1),
    ],
    "PRESCRIPTION": [
        (r"PRESCRIPTION", 3),                       # "Rx" alone is weak: insurance cards print "Rx Tier"
        (r"\bRx\b|\bSIG\s*:|REFILLS?|DISPENSE|\bNDC\b", 1),
    ],
    "POLICY_AMENDMENT": [
        (r"POLICY\s*AMENDMENT|ENDORSEMENT|AMENDMENT\s*TYPE", 3),
        (r"\bRIDER\b|EFFECTIVE\s*DATE|AMENDMENT", 1),
    ],
}


def classify_text(raw_text: str) -> dict:
    scores: dict[str, int] = {}
    first_hit: dict[str, float] = {}
    for doc_type, patterns in MARKERS.items():
        score, earliest = 0, float("inf")
        for pattern, weight in patterns:
            m = re.search(pattern, raw_text, re.IGNORECASE)
            if m:
                score += weight
                earliest = min(earliest, m.start())
        scores[doc_type] = score
        first_hit[doc_type] = earliest

    ranked = sorted(scores, key=lambda t: (scores[t], -first_hit[t]), reverse=True)
    best, runner_up = ranked[0], ranked[1]
    if scores[best] == 0:
        return {"doc_type": "UNKNOWN", "confidence": 0.0, "routing_tags": []}
    if scores[best] == scores[runner_up]:
        confidence = 0.6                              # tie: position decided, signal is weak
    else:
        confidence = round(min(1.0, 0.5 + 0.1 * (scores[best] - scores[runner_up])), 2)
    return {"doc_type": best, "confidence": confidence, "routing_tags": [best.lower()]}


@node("classifier", writes="classifier")
def classifier_agent(state: CaseState) -> dict:
    prep = state.get("preprocessor") or {}
    if "error" in prep:
        raise RuntimeError(f"preprocessor failed: {prep['error']}")

    override = state.get("doc_type_override")
    if override:
        return {"doc_type": override, "confidence": 1.0, "routing_tags": [override.lower()],
                "source": "human_override"}

    result = classify_text(prep.get("raw_text", ""))
    if result["confidence"] < config.AGENT_CONFIDENCE_MIN:
        result["doc_type"] = "UNKNOWN"
    result["source"] = "markers"
    return result
