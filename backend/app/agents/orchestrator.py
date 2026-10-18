"""
Stage 6 -- final decision. Runs as the last LangGraph node (defer=True, so it waits
for every branch that feeds it).

Precedence: ESCALATE > REJECT > APPROVE (WORKFLOW.md decision D6).
An agent absent from the state did not run for this document type, so it is
not applicable. An agent that errored means ESCALATE, because we cannot tell
what its answer would have been.
"""

from __future__ import annotations

from app import config
from app.agents._base import node
from app.state import CaseState

AGENT_KEYS = ["preprocessor", "classifier", "kyc", "claims", "policy", "fraud"]
CONFIDENT_KEYS = ["preprocessor", "classifier", "kyc", "policy"]


def decide(state: CaseState) -> dict:
    outputs = {k: state.get(k) for k in AGENT_KEYS}

    errors = {k: v["error"] for k, v in outputs.items() if isinstance(v, dict) and "error" in v}
    if errors:
        return _out("ESCALATE", 0.0,
                    "agent error(s): " + "; ".join(f"{k}: {m}" for k, m in errors.items()), outputs)

    cls = outputs["classifier"] or {}
    if cls.get("doc_type") in (None, "UNKNOWN"):
        return _out("ESCALATE", cls.get("confidence", 0.0),
                    "document type not recognised; human review", outputs)

    present = [outputs[k] for k in CONFIDENT_KEYS if outputs[k] and "confidence" in outputs[k]]
    confidences = [o["confidence"] for o in present]
    low = [k for k in CONFIDENT_KEYS
           if outputs[k] and outputs[k].get("confidence", 1.0) < config.AGENT_CONFIDENCE_MIN]
    if low:
        why = f"low confidence from: {', '.join(low)}"
        if outputs["preprocessor"] and outputs["preprocessor"].get("is_blurry"):
            why += " (scan is blurry)"
        return _out("ESCALATE", min(confidences), why, outputs)

    fraud = outputs["fraud"] or {}
    if fraud.get("fraud_score", 0.0) >= config.FRAUD_ESCALATE_MIN:
        kinds = ", ".join(a["type"] for a in fraud.get("anomalies", []))
        return _out("ESCALATE", min(confidences),
                    f"fraud score {fraud['fraud_score']} ({kinds})", outputs)

    kyc = outputs["kyc"] or {}
    if kyc and not kyc.get("kyc_passed", True):
        return _out("REJECT", min(confidences),
                    "KYC failed: " + ", ".join(kyc.get("flags", [])), outputs)

    claims = outputs["claims"] or {}
    if claims.get("kind") == "CLAIM" and not claims.get("schema_valid", True):
        errs = ", ".join(f"{e['code']} on {e['field']}" for e in claims.get("validation_errors", []))
        return _out("REJECT", min(confidences), f"claim schema invalid: {errs}", outputs)

    policy = outputs["policy"] or {}
    if policy and not policy.get("covered", True):
        return _out("REJECT", min(confidences),
                    "procedure not covered: " + "; ".join(policy.get("exclusions", [])), outputs)

    return _out("APPROVE", min(confidences) if confidences else 0.9,
                "all applicable checks passed", outputs)


def _out(decision: str, confidence: float, justification: str, outputs: dict) -> dict:
    summaries = {}
    for k, v in outputs.items():
        if v is None:
            summaries[k] = "not run"
        elif "error" in v:
            summaries[k] = f"error: {v['error']}"
        else:
            summaries[k] = "ok"
    return {
        "decision": decision,
        "confidence": round(confidence, 2),
        "justification": justification,
        "needs_review": decision == "ESCALATE",
        "agent_summaries": summaries,
    }


@node("orchestrator", writes="orchestrator")
def orchestrator_agent(state: CaseState) -> dict:
    return decide(state)
