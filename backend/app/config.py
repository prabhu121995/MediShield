"""
Runtime configuration. Every setting is read from the project-root .env; nothing is set in
code. A missing required key stops the app at startup with the list of missing keys, so a
setting is never silently replaced by a value buried in a .py file.

Business rules (document types, field schemas, thresholds used by the claim agents, fraud
parameters) live in the JSON file named by PIPELINE_RULES. Relative paths in .env are
resolved from the project root.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

_missing: list[str] = []


def _raw(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or not value.strip():
        _missing.append(name)
        return None
    return value.strip()


def env_str(name: str) -> str:
    return _raw(name) or ""


def env_int(name: str) -> int:
    value = _raw(name)
    return int(value) if value is not None else 0


def env_float(name: str) -> float:
    value = _raw(name)
    return float(value) if value is not None else 0.0


def env_list(name: str) -> list[str]:
    value = _raw(name)
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def env_path(name: str) -> Path:
    value = _raw(name)
    path = Path(value) if value else Path()
    return path if path.is_absolute() else PROJECT_ROOT / path


def env_optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


# ---- models -----------------------------------------------------------------------
LLM_PROVIDER = env_str("LLM_PROVIDER").lower()            # "gemini" (hosted), "groq" (hosted) or "ollama" (local)
GROQ_API_KEY = env_optional("GROQ_API_KEY")
GEMINI_API_KEY = env_optional("GEMINI_API_KEY")
if LLM_PROVIDER == "ollama":
    MODEL_NAME = env_str("OLLAMA_MODEL_NAME")
    VISION_MODEL_NAME = env_str("OLLAMA_VISION_MODEL_NAME")
elif LLM_PROVIDER == "gemini":
    MODEL_NAME = env_str("GEMINI_MODEL_NAME")
    VISION_MODEL_NAME = env_str("GEMINI_VISION_MODEL_NAME")
else:
    MODEL_NAME = env_str("MODEL_NAME")
    VISION_MODEL_NAME = env_str("VISION_MODEL_NAME")
OLLAMA_HOST = env_str("OLLAMA_HOST").rstrip("/")
_think = (env_optional("OLLAMA_THINK") or "").lower()
OLLAMA_THINK = True if _think == "true" else False if _think == "false" else None
OLLAMA_NUM_CTX = env_int("OLLAMA_NUM_CTX")
OLLAMA_TIMEOUT_S = env_int("OLLAMA_TIMEOUT_S")
OLLAMA_MAX_TOKENS_TEXT = env_int("OLLAMA_MAX_TOKENS_TEXT")
OLLAMA_MAX_TOKENS_VISION = env_int("OLLAMA_MAX_TOKENS_VISION")
# Gemini: tried in order when a model is rate limited or overloaded (429 / 5xx)
GEMINI_FALLBACK_MODELS = [m.strip() for m in (env_optional("GEMINI_FALLBACK_MODELS") or "").split(",") if m.strip()]
GEMINI_THINKING_LEVEL = env_optional("GEMINI_THINKING_LEVEL")   # low | medium | high; unset: the model's default
GEMINI_TIMEOUT_S = env_int("GEMINI_TIMEOUT_S") if LLM_PROVIDER == "gemini" else 0
LLM_RETRY_ATTEMPTS = env_int("LLM_RETRY_ATTEMPTS")
LLM_MAX_RATE_LIMIT_WAIT_S = env_int("LLM_MAX_RATE_LIMIT_WAIT_S")
VISION_MAX_SIDE = env_int("VISION_MAX_SIDE")
VISION_JPEG_QUALITY = env_int("VISION_JPEG_QUALITY")
EMBED_MODEL = env_str("EMBED_MODEL")
POLICY_COLLECTION = env_str("COLLECTION_NAME")

# ---- MySQL (every record: members, claims, pages, decisions, audit, checkpoints, caches) --
MYSQL_HOST = env_str("MYSQL_HOST")
MYSQL_PORT = env_int("MYSQL_PORT")
MYSQL_USER = env_str("MYSQL_USER")
MYSQL_PASSWORD = env_str("MYSQL_PASSWORD")
MYSQL_DB = env_str("MYSQL_DB")
MYSQL_CONNECT_TIMEOUT_S = env_int("MYSQL_CONNECT_TIMEOUT_S")

# ---- files ------------------------------------------------------------------------
DATASET_DIR = env_path("DATASET_DIR")
STORAGE_DIR = env_path("STORAGE_DIR")
UPLOAD_DIR = env_path("UPLOAD_DIR")
POLICY_PDF_DIR = env_path("POLICY_PDF_DIR")
QDRANT_PATH = env_path("QDRANT_PATH")
RULES_PATH = env_path("PIPELINE_RULES")

# ---- uploads and API --------------------------------------------------------------
MAX_UPLOAD_BYTES = env_int("MAX_UPLOAD_MB") * 1024 * 1024
MAX_PDF_PAGES = env_int("MAX_PDF_PAGES")
PDF_RENDER_DPI = env_int("PDF_RENDER_DPI")
UPLOAD_EXTENSIONS = set(env_list("UPLOAD_EXTENSIONS"))              # claim uploads (PDF allowed)
SINGLE_UPLOAD_EXTENSIONS = set(env_list("SINGLE_UPLOAD_EXTENSIONS"))  # one-page image uploads
MAX_BATCH_FILES = env_int("MAX_BATCH_FILES")
MAX_CLAIM_PAGES = env_int("MAX_CLAIM_PAGES")
LIST_LIMIT_DEFAULT = env_int("LIST_LIMIT_DEFAULT")
LIST_LIMIT_MAX = env_int("LIST_LIMIT_MAX")
CORS_ORIGINS = env_list("CORS_ORIGINS")
PIPELINE_WORKERS = env_int("PIPELINE_WORKERS")

# ---- auth -------------------------------------------------------------------------
JWT_SECRET = env_str("JWT_SECRET")
JWT_TTL_HOURS = env_int("JWT_TTL_HOURS")
PASSWORD_HASH_ITERATIONS = env_int("PASSWORD_HASH_ITERATIONS")

# ---- policy retrieval and assistant -----------------------------------------------
POLICY_MAX_CLAUSE_CHARS = env_int("POLICY_MAX_CLAUSE_CHARS")
POLICY_CHUNK_TOKENS = env_int("POLICY_CHUNK_TOKENS")
POLICY_CHAT_MAX_HISTORY_TURNS = env_int("POLICY_CHAT_MAX_HISTORY_TURNS")
POLICY_CHAT_MAX_CLAUSE_CHARS = env_int("POLICY_CHAT_MAX_CLAUSE_CHARS")
POLICY_CHAT_MAX_SOURCES = env_int("POLICY_CHAT_MAX_SOURCES")

# ---- single-document pipeline thresholds (the claim pipeline reads PIPELINE_RULES) --
AGENT_CONFIDENCE_MIN = env_float("AGENT_CONFIDENCE_MIN")
FRAUD_ESCALATE_MIN = env_float("FRAUD_ESCALATE_MIN")
BLUR_VARIANCE_MIN = env_float("BLUR_VARIANCE_MIN")

# ---- logs (MySQL table app_log) ---------------------------------------------------
LOG_LEVEL = env_str("LOG_LEVEL")
LOG_BATCH_SIZE = env_int("LOG_BATCH_SIZE")
LOG_FLUSH_S = env_float("LOG_FLUSH_S")
LOG_EXPORT_MAX_ROWS = env_int("LOG_EXPORT_MAX_ROWS")
LOG_STATS_SCAN_MAX = env_int("LOG_STATS_SCAN_MAX")
LOG_TOP_N = env_int("LOG_TOP_N")

# ---- scripts ----------------------------------------------------------------------
API_URL = env_str("API_URL")
DATASET_RUN_TIMEOUT_S = env_int("DATASET_RUN_TIMEOUT_S")

if _missing:
    raise RuntimeError("missing settings in .env: " + ", ".join(dict.fromkeys(_missing))
                       + f"  (see {PROJECT_ROOT / '.env.example'})")


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


RULES = load_rules()
