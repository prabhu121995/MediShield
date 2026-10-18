"""
Page reading, in three steps, each cached:

    transcribe(image)  vision model: image -> text, plus image quality      (preprocessor)
    classify(image)    vision model: which document type, or UNKNOWN          (classifier)
    extract(text,type) text model: that type's fields from the transcription  (extractor)

Models and provider come from .env (LLM_PROVIDER, OLLAMA_VISION_MODEL_NAME / VISION_MODEL_NAME,
OLLAMA_MODEL_NAME / MODEL_NAME). Document types, descriptions and field schemas come from
pipeline_rules.json. Nothing about the forms' layout is coded here.

Outputs are cached in MySQL (model_cache), keyed by input + model + prompt, so editing the
rules or switching models reads the page again.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from app import config, db
from app.agents._util import parse_date
from app.llm import chat_json, vision_json

UNKNOWN = "UNKNOWN"
QUALITIES = ("clear", "blurry", "unreadable")

TRANSCRIBE_PROMPT = """Transcribe this scanned document page exactly as printed, top to bottom.
Keep labels next to their values, one line per printed line. Copy every word, number, code and
date as written, including any stamps or warnings. Do not summarise, correct or add anything.

Return ONLY this JSON object:
{"text": "<the transcription, lines separated by \\n>",
 "image_quality": "clear" | "blurry" | "unreadable"}"""


def classify_prompt(rules: dict) -> str:
    catalogue = "\n".join(f"- {name}: {spec['description']}" for name, spec in rules["document_types"].items())
    return f"""Look at this scanned page from a health insurance claim and decide what kind of
document it is. Choose exactly one of these types, or {UNKNOWN} if it is none of them or you
cannot tell:
{catalogue}

Return ONLY this JSON object:
{{"doc_type": "<type or {UNKNOWN}>", "confidence": <0..1>,
  "image_quality": "clear" | "blurry" | "unreadable"}}"""


def extract_prompt(doc_type: str, rules: dict) -> str:
    spec = rules["document_types"][doc_type]
    return f"""You are given the transcribed text of one {doc_type} ({spec['description']}).
Extract these fields: {json.dumps(spec['fields'])}
Copy values exactly as written; dates as YYYY-MM-DD; use null for anything not in the text;
never guess. Also list in "fields_marked_missing" every field the text itself shows as
missing, blank, omitted or illegible.

Return ONLY this JSON object:
{{"fields": {{...}}, "fields_marked_missing": ["<field>", ...]}}"""


def _cached(kind: str, parts: list[bytes], model: str, call: Callable[[], dict]) -> dict:
    h = hashlib.sha256(kind.encode())
    for p in parts:
        h.update(p)
    key = f"{kind}-{h.hexdigest()}"
    hit = db.cache_get(key)
    if hit is not None:
        return hit
    out = call()
    db.cache_put(key, kind, model, out)
    return out


def _file_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def _quality(value) -> str:
    q = str(value or "clear").strip().lower()
    return q if q in QUALITIES else "clear"


# ---- the three steps -----------------------------------------------------------------

def transcribe(path: str) -> dict[str, Any]:
    """Preprocessor: the page as text, read by the vision model."""
    raw = _cached("text", [_file_bytes(path), config.VISION_MODEL_NAME.encode(), TRANSCRIBE_PROMPT.encode()],
                  config.VISION_MODEL_NAME, lambda: vision_json(path, TRANSCRIBE_PROMPT))
    return {"text": str(raw.get("text") or "").strip(), "image_quality": _quality(raw.get("image_quality")),
            "model": config.VISION_MODEL_NAME}


def classify(path: str, rules: dict | None = None) -> dict[str, Any]:
    """Classifier: the document type (a type from the rules) or UNKNOWN."""
    rules = rules or config.RULES
    prompt = classify_prompt(rules)
    raw = _cached("type", [_file_bytes(path), config.VISION_MODEL_NAME.encode(), prompt.encode()],
                  config.VISION_MODEL_NAME, lambda: vision_json(path, prompt))
    doc_type = str(raw.get("doc_type") or UNKNOWN).strip().upper().replace(" ", "_")
    try:
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.0
    return {"doc_type": doc_type if doc_type in rules["document_types"] else UNKNOWN,
            "confidence": confidence, "image_quality": _quality(raw.get("image_quality")),
            "model": config.VISION_MODEL_NAME}


def extract(text: str, doc_type: str, rules: dict | None = None) -> dict[str, Any]:
    """Extractor: the fields of `doc_type`, read from the transcription by the text model."""
    rules = rules or config.RULES
    if doc_type not in rules["document_types"] or not text.strip():
        return {"fields": {}, "fields_marked_missing": []}
    prompt = extract_prompt(doc_type, rules)
    raw = _cached("fields", [text.encode(), config.MODEL_NAME.encode(), prompt.encode()],
                  config.MODEL_NAME, lambda: chat_json(prompt, text))
    return normalize_fields(raw, doc_type, rules)


# ---- type coercion ---------------------------------------------------------------------

def _as_list(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,;]", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _as_number(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def normalize_fields(raw: dict, doc_type: str, rules: dict) -> dict[str, Any]:
    """Coerce the model's JSON to the field types declared in the rules."""
    schema = rules["document_types"][doc_type]["fields"]
    given = raw.get("fields") or {}
    fields: dict[str, Any] = {}
    for name, kind in schema.items():
        value = given.get(name)
        if kind.startswith("date"):
            parsed = parse_date(value)
            value = parsed.isoformat() if parsed else None
        elif kind.startswith("number"):
            value = _as_number(value)
        elif kind.startswith("list"):
            value = _as_list(value)
        elif kind.startswith("boolean"):
            value = value if isinstance(value, bool) else str(value).strip().lower() in ("true", "yes", "1")
        else:
            value = str(value).strip() if value not in (None, "", "null") else None
        fields[name] = value
    return {"fields": fields, "fields_marked_missing": _as_list(raw.get("fields_marked_missing"))}
