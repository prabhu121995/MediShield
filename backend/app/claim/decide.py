"""
Orchestrator, second half: the final decision. A deferred LangGraph node, so it runs once,
after every upstream branch has written its output into the shared state.

    ESCALATE  the patient already has a stored claim with the same DOS and amount (duplicate;
              the duplicate_check skipped every agent)
              OR fraud score >= 0.3 (the scored fraud signals)
              OR any agent confidence < 0.6 (a page, kyc, claims, policy, fraud)
              OR an agent errored, a page is UNKNOWN, or a required document is missing
              OR a guardrail fired on a page (injected instructions, impossible values)
    REJECT    KYC failed (the claimant's name is not a member, or the insurance card is not
              valid) OR a procedure is not covered OR the schema is invalid (the claim form
              lacks a required field; a prescription's missing fields are only tracked)
    APPROVE   KYC passed + claim valid + covered + fraud score < 0.3

Precedence: KYC failed or a procedure not covered REJECTS outright, even with a fraud score
>= 0.3 or another escalation; those are kept in the reasons ("also flagged: ...") so the fraud
stays visible. Otherwise ESCALATE > REJECT (schema invalid) > APPROVE: a human look is cheaper
than wrongly rejecting a policyholder over a form.

Confidence is the weighted mean of the confidences of the agents that ran. Thresholds and
weights: pipeline_rules.json ("thresholds", "confidence_weights").

Output: {decision, confidence, justification, agent_summaries, reasons, needs_review}
"""

from __future__ import annotations

from app import config
from app.claim._node import claim_node
from app.claim.state import ClaimState

AGENTS = ["kyc", "claims", "policy", "fraud"]


def _ingest_confidence(docs: list[dict]) -> float | None:
    if not docs:
        return None
    return min(min(d.get("read_confidence", 0.0), d.get("classifier_confidence", 0.0)) for d in docs)


def agent_summaries(state: ClaimState) -> dict[str, dict]:
    docs = state.get("docs") or []
    out = {"ingest": {
        "status": "error" if any(d.get("error") for d in docs) else "ok",
        "confidence": _ingest_confidence(docs),
        "summary": f"{len(docs)} page(s) read: " + ", ".join(
            f"{d.get('file_name')} -> {d.get('doc_type')}" for d in docs),
    }}
    for key in AGENTS:
        v = state.get(key)
        if v is None:
            out[key] = {"status": "not_run", "confidence": None, "summary": "not applicable to these documents"}
        elif "error" in v:
            out[key] = {"status": "error", "confidence": 0.0, "summary": v["error"]}
        else:
            out[key] = {"status": "ok", "confidence": v.get("confidence"), "summary": v.get("summary", "")}
    return out


def decide(state: ClaimState) -> dict:
    summaries = agent_summaries(state)
    plan = state.get("plan") or {}
    kyc = state.get("kyc") or {}
    claims = state.get("claims") or {}
    policy = state.get("policy") or {}
    fraud = state.get("fraud") or {}

    weights = config.RULES["confidence_weights"]
    th = config.RULES["thresholds"]
    ran = {k: s["confidence"] for k, s in summaries.items() if s["status"] != "not_run" and s["confidence"] is not None}
    weight = sum(weights[k] for k in ran)
    confidence = round(sum(weights[k] * c for k, c in ran.items()) / weight, 3) if weight else 0.0

    escalate, reject = [], []
    dup = state.get("duplicate") or {}
    if dup.get("is_duplicate"):
        escalate.append(dup.get("summary") or f"duplicate of claim {dup.get('duplicate_of')}")
    for k, s in summaries.items():
        if s["status"] == "error":
            escalate.append(f"{k} agent error: {s['summary']}")
        elif s["confidence"] is not None and s["confidence"] < th["agent_confidence_min"]:
            escalate.append(f"low confidence from {k} ({s['confidence']:.2f})")
    for d in state.get("docs") or []:
        if d.get("is_blurry"):
            escalate.append(f"{d.get('file_name')} is blurry (model: {d.get('image_quality')}, Laplacian variance {d.get('blur_variance')})")
        for g in d.get("guardrail_flags") or []:
            escalate.append(f"guardrail on {d.get('file_name')}: {g['code']} {g['message']}")
    if plan.get("unknown_documents"):
        escalate.append("unrecognised document(s): " + ", ".join(plan["unknown_documents"]))
    if plan.get("missing_required"):
        escalate.append("missing required document(s): " + ", ".join(plan["missing_required"]))
    if fraud.get("fraud_score", 0.0) >= th["fraud_escalate_min"]:
        escalate.append(f"fraud score {fraud['fraud_score']:.2f} ({', '.join(fraud.get('tags', []))})")

    if kyc and "error" not in kyc and kyc.get("kyc_passed") is False:
        reject.append("KYC failed: " + ", ".join(kyc.get("fail_reasons") or kyc.get("flags", [])))
    if policy and "error" not in policy and policy.get("covered") is False:
        reject.append("procedure not covered: " + "; ".join(policy.get("exclusions", [])))
    hard_reject = bool(reject)                     # KYC failed / not covered outweigh any escalation
    for m in fraud.get("missing_fields") or []:
        if m.get("doc_type") == "CLAIM_FORM":
            reject.append(f"schema invalid: {m['detail']}")

    if hard_reject:
        decision, reasons = "REJECT", reject + [f"also flagged: {e}" for e in escalate]
    elif escalate:
        decision, reasons = "ESCALATE", escalate + reject
    elif reject:
        decision, reasons = "REJECT", reject
    else:
        decision, reasons = "APPROVE", ["KYC passed, claim valid, all procedures covered, fraud score "
                                        f"{fraud.get('fraud_score', 0.0):.2f} < {th['fraud_escalate_min']}"]

    lead = {"ESCALATE": "Escalated to human review", "REJECT": "Rejected", "APPROVE": "Approved"}[decision]
    return {
        "decision": decision,
        "confidence": confidence,
        "justification": f"{lead}: " + "; ".join(reasons),
        "reasons": reasons,
        "agent_summaries": summaries,
        "weights": {k: weights[k] for k in ran},
        "needs_review": decision == "ESCALATE",
    }


@claim_node("decide", writes="decision")
def decide_agent(state: ClaimState) -> dict:
    return decide(state)
