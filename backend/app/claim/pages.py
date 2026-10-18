"""
The document queue, in two passes over the pages, one page at a time.

    pass 1  classify   in page_order of the expected type (the type chosen at upload, else a
                       filename_hints word in the file name; no hint: last). vision model: document type from the rules, or UNKNOWN
                       (a low confidence is UNKNOWN; a reviewer's correction is used as-is)
                       + image quality and blur score
    pass 2  extract    in page_order of the classified type (ID, claim form, discharge,
                       prescription, policy amendment; upload order within a type, UNKNOWN last).
                       Preprocess and extract in one step: vision model image -> text (shown
                       in the UI), then the text model reads that type's fields from it

Every page is classified before any page is read. If a page is UNKNOWN, blurry or could not
be classified, the claim stops at the hold (app.claim.hold) and nothing is read until a
person continues or re-uploads. A page that cannot be read becomes UNKNOWN with an error;
it does not stop the claim.
"""

from __future__ import annotations

import re
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


def _step(state: ClaimState, agent: str, doc: dict, base: dict | None, work) -> tuple[dict, dict]:
    """Run one page step with timing, a trace event and audit rows (started, completed).
    `doc` is the page to work on; `base` what earlier passes know about it."""
    check_cancelled(state)
    page = dict(base or {"doc_id": doc["doc_id"], "file_name": doc.get("file_name", "")})
    started(state, agent, doc_id=doc["doc_id"], file_name=page.get("file_name"))
    started_at, t0 = audit.now(), time.perf_counter()
    if page.get("error"):
        result, own = page, {"skipped": True}           # an earlier pass failed: pass the page through
    else:
        try:
            own = work(doc, page)
            result = {**page, **own}
        except Exception as exc:
            own = {}
            result = {**page, "doc_type": "UNKNOWN", "classifier_confidence": page.get("classifier_confidence", 0.0),
                      "fields": {}, "fields_marked_missing": [], "read_confidence": 0.0,
                      "error": f"{agent} failed: {type(exc).__name__}: {exc}", "failed_step": agent}
    event = emit(state, agent, started_at, t0, result, doc_id=doc["doc_id"], returned=own)
    event["note"] = event["note"] or f"{result.get('file_name')}"
    return {"trace": [event], "status": agent.upper()}, result


def _rank(doc_type: str | None) -> int:
    order = config.RULES.get("page_order") or []
    return order.index(doc_type) if doc_type in order else len(order)


def expected_type(doc: dict) -> str | None:
    """The type a page should be before the model looks: the uploader's choice, else a hint word
    from the file name ("id_PT_1.png" -> ID_DOCUMENT). Only orders the queue; it decides nothing."""
    if doc.get("doc_type_override"):
        return doc["doc_type_override"]
    words = set(re.split(r"[^a-z0-9]+", (doc.get("file_name") or "").lower()))
    for doc_type, hints in (config.RULES.get("filename_hints") or {}).items():
        if words & set(hints):
            return doc_type
    return None


def in_processing_order(rows: list[dict]) -> list[dict]:
    """Stored page rows (upload order) in the order the claim processes them: by the classified
    type once known, else the expected type; UNKNOWN and pages with no hint last."""
    def key(i_row):
        i, row = i_row
        doc_type = row.get("doc_type") or expected_type(
            {"doc_type_override": (row.get("override") or {}).get("doc_type"), "file_name": row.get("file_name")})
        return _rank(doc_type), i
    return [row for _, row in sorted(enumerate(rows), key=key)]


def next_to_classify(state: ClaimState) -> dict:
    """The page to classify next: by page_order of its expected type, then upload order."""
    done = {p["doc_id"] for p in state.get("classified") or []}
    waiting = [(i, d) for i, d in enumerate(state.get("documents") or []) if d["doc_id"] not in done]
    return min(waiting, key=lambda idoc: (_rank(expected_type(idoc[1])), idoc[0]))[1]


def next_to_read(state: ClaimState) -> dict:
    """The classified page to read next: by page_order of its classified type, then upload order."""
    upload = {d["doc_id"]: i for i, d in enumerate(state.get("documents") or [])}
    read = {d["doc_id"] for d in state.get("docs") or []}
    unread = [p for p in state.get("classified") or [] if p["doc_id"] not in read]
    return min(unread, key=lambda p: (_rank(p.get("doc_type")), upload.get(p["doc_id"], 0)))


def classify_agent(state: ClaimState) -> dict:
    def work(doc, page):
        th = config.RULES["thresholds"]
        variance = blur_of(doc["file_path"])
        override = doc.get("doc_type_override")
        if override:                                    # a reviewer said what it is: no model call
            blurry = variance < th["blur_variance_min"]
            return {"doc_type": override, "classifier_confidence": 1.0, "classifier_source": "human_override",
                    "image_quality": "clear", "blur_variance": round(variance, 2), "is_blurry": blurry,
                    "read_confidence": th["blurry_page_confidence"] if blurry else 0.9}
        out = classify(doc["file_path"])
        blurry = variance < th["blur_variance_min"] or out["image_quality"] != "clear"
        doc_type = out["doc_type"] if out["confidence"] >= th["classifier_confidence_min"] else "UNKNOWN"
        return {"doc_type": doc_type, "classified_as": out["doc_type"],
                "classifier_confidence": round(out["confidence"], 3), "classifier_source": "vision",
                "image_quality": out["image_quality"], "blur_variance": round(variance, 2), "is_blurry": blurry,
                "model": out["model"],
                "read_confidence": 0.0 if out["image_quality"] == "unreadable"
                else th["blurry_page_confidence"] if blurry else 0.9}
    update, result = _step(state, "classify", next_to_classify(state), None, work)
    return {**update, "classified": [result]}


def extract_agent(state: ClaimState) -> dict:
    """Preprocess + extract: the page as text, then that type's fields from the text."""
    def work(doc, page):
        read = transcribe(doc["file_path"])
        out = extract(read["text"], page.get("doc_type") or "UNKNOWN")
        own = {"raw_text": read["text"], "text_chars": len(read["text"]), "text_quality": read["image_quality"],
               **out, "fields_extracted": sorted(k for k, v in (out.get("fields") or {}).items()
                                                 if v not in (None, "", [], {}))}
        if read["image_quality"] != "clear":
            own["read_confidence"] = min(page.get("read_confidence", 0.9),
                                         config.RULES["thresholds"]["blurry_page_confidence"])
        return own
    base = next_to_read(state)
    doc = next(d for d in state.get("documents") or [] if d["doc_id"] == base["doc_id"])
    update, result = _step(state, "extract", doc, base, work)
    result.setdefault("fields", {})
    result.setdefault("fields_marked_missing", [])
    return {**update, "docs": [result]}                # the page is finished
