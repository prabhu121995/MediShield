"""
Stage 1 -- document image to content.

One vision call (Ollama for testing, Groq in production) reads the page: all text, plus the identity, policy and
date fields the other agents need. Blur is measured separately on the pixels,
because a vision model will happily read a blurry page and give no warning.

A blurry page is not an error. It lowers confidence so the orchestrator
escalates to a human (HIL).
"""

from __future__ import annotations

from app import config
from app.agents._base import node
from app.blur import laplacian_variance
from app.llm import vision_json
from app.state import CaseState

FIELD_KEYS = ["member_id", "policy_number", "patient_name", "date_of_birth",
              "id_expiry_date", "service_date", "admission_date", "discharge_date",
              "prescription_date"]

PROMPT = """You read one insurance document image (claim form, ID or insurance card,
discharge summary, prescription, or policy amendment).

Return ONLY a JSON object with these keys:
- "raw_text": all text printed on the document, line by line. Keep labels and values.
- "fields": an object with these keys, each a string or null:
  member_id, policy_number, patient_name, date_of_birth (YYYY-MM-DD),
  id_expiry_date (YYYY-MM-DD), service_date (YYYY-MM-DD, date of treatment),
  admission_date (YYYY-MM-DD), discharge_date (YYYY-MM-DD),
  prescription_date (YYYY-MM-DD).

Rules: convert MM/DD/YYYY to YYYY-MM-DD. Use null for anything not printed.
Do not guess or infer values that are not on the page."""


def read_document(image_path: str) -> dict:
    """Vision read of one page. Isolated so tests can replace it."""
    data = vision_json(image_path, PROMPT)
    fields = data.get("fields") or {}
    clean = {k: str(fields[k]).strip() for k in FIELD_KEYS
             if fields.get(k) not in (None, "", "null")}
    return {"raw_text": str(data.get("raw_text") or ""), "fields": clean}


def blur_score(image_path: str) -> float:
    return laplacian_variance(image_path)


@node("preprocessor", writes="preprocessor")
def preprocessor_agent(state: CaseState) -> dict:
    doc = read_document(state["file_path"])
    variance = blur_score(state["file_path"])
    is_blurry = variance < config.BLUR_VARIANCE_MIN
    return {
        "raw_text": doc["raw_text"],
        "fields": doc["fields"],
        "bounding_boxes": [],                       # vision model gives no boxes
        "blur_variance": round(variance, 2),
        "is_blurry": is_blurry,
        # Blurry pages are low-confidence by definition -> orchestrator escalates to HIL.
        "confidence": 0.4 if is_blurry else 0.9,
    }
