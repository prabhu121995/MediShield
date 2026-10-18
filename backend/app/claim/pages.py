"""
The document queue: every page passes three nodes in turn, one page at a time, in upload order.

    preprocess  vision model: image -> text (shown in the UI), image quality; + blur score
    classify    vision model: document type from the rules, or UNKNOWN
                (a low confidence is UNKNOWN; a reviewer's correction is used as-is)
    extract     text model: that type's fields from the transcription

The graph loops preprocess -> classify -> extract until every page is done. A page that
cannot be read becomes UNKNOWN with an error; it does not stop the claim. Blurry or
unclassifiable pages are escalated by the orchestrator.
"""

from __future__ import annotations

import time

from app import audit, config
from app.blur import laplacian_variance
from app.claim import vision
from app.claim._node import check_cancelled, emit, started
from app.claim.state import ClaimState

# Isolated so tests can replace them.
transcribe = vision.transcribe
classify = vision.classify
extract = vision.extract


def blur_of(path: str) -> float:
    return laplacian_variance(path)


def _current(state: ClaimState) -> dict:
    return (state.get("documents") or [])[len(state.get("docs") or [])]


def _step(state: ClaimState, agent: str, work) -> dict:
    """Run one page step with timing, a trace event and audit rows (started, completed)."""
    check_cancelled(state)
    doc = _current(state)
    page = dict(state.get("page") or {}) if (state.get("page") or {}).get("doc_id") == doc["doc_id"] else         {"doc_id": doc["doc_id"], "file_name": doc.get("file_name", "")}
    started(state, agent, doc_id=doc["doc_id"], file_name=page.get("file_name"))
    started_at, t0 = audit.now(), time.perf_counter()
    if page.get("error"):
        result, own = page, {"skipped": True}           # an earlier step failed: pass the page through
    else:
        try:
            own = work(doc, page)
            result = {**page, **own}
        except Exception as exc:
            own = {}
            result = {**page, "doc_type": "UNKNOWN", "classifier_confidence": 0.0, "fields": {},
                      "fields_marked_missing": [], "read_confidence": 0.0,
                      "error": f"{agent} failed: {type(exc).__name__}: {exc}"}
    event = emit(state, agent, started_at, t0, result, doc_id=doc["doc_id"], returned=own)
    event["note"] = event["note"] or f"{result.get('file_name')}"
    return {"page": result, "trace": [event], "status": agent.upper()}, result


def preprocess_agent(state: ClaimState) -> dict:
    def work(doc, page):
        th = config.RULES["thresholds"]
        read = transcribe(doc["file_path"])
        variance = blur_of(doc["file_path"])
        blurry = variance < th["blur_variance_min"] or read["image_quality"] != "clear"
        return {"raw_text": read["text"], "text_chars": len(read["text"]), "image_quality": read["image_quality"],
                "blur_variance": round(variance, 2), "is_blurry": blurry, "model": read["model"],
                "read_confidence": 0.0 if read["image_quality"] == "unreadable"
                else th["blurry_page_confidence"] if blurry else 0.9}
    update, _ = _step(state, "preprocess", work)
    return update


def classify_agent(state: ClaimState) -> dict:
    def work(doc, page):
        override = doc.get("doc_type_override")
        if override:
            return {"doc_type": override, "classifier_confidence": 1.0, "classifier_source": "human_override"}
        out = classify(doc["file_path"])
        doc_type = out["doc_type"]
        if out["confidence"] < config.RULES["thresholds"]["classifier_confidence_min"]:
            doc_type = "UNKNOWN"
        extra = {}
        if out["image_quality"] != "clear" and not page.get("is_blurry"):
            extra = {"is_blurry": True, "read_confidence": min(page.get("read_confidence", 0.9),
                                                               config.RULES["thresholds"]["blurry_page_confidence"])}
        return {"doc_type": doc_type, "classified_as": out["doc_type"],
                "classifier_confidence": round(out["confidence"], 3), "classifier_source": "vision", **extra}
    update, _ = _step(state, "classify", work)
    return update


def extract_agent(state: ClaimState) -> dict:
    def work(doc, page):
        out = extract(page.get("raw_text") or "", page.get("doc_type") or "UNKNOWN")
        return {**out, "fields_extracted": sorted(k for k, v in (out.get("fields") or {}).items()
                                                  if v not in (None, "", [], {}))}
    update, result = _step(state, "extract", work)
    result.setdefault("fields", {})
    result.setdefault("fields_marked_missing", [])
    return {**update, "docs": [result]}                # the page is finished
