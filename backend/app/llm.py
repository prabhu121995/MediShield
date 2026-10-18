"""
LLM helpers: JSON chat, and JSON from a vision model reading an image.

The provider comes from LLM_PROVIDER in .env:
- "ollama": a local model through the Ollama HTTP API. Used for testing. Slow on CPU,
  which is fine for testing.
- "groq": a hosted model through the Groq API (the original setup, kept for production).

Every call retries with backoff, so a rate limit or a bad JSON reply does not crash the case.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from app import config

_client = None


def _groq():
    global _client
    if _client is None:
        from groq import Groq
        if not config.GROQ_API_KEY:
            raise RuntimeError("GROQ_API_KEY is not set in .env")
        _client = Groq(api_key=config.GROQ_API_KEY)
    return _client


def parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    return json.loads(cleaned)


_RETRY_AFTER = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s")


def _retry_wait(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if waiting cannot help."""
    text = str(exc)
    m = _RETRY_AFTER.search(text)
    if m:
        wait = int(m.group(1) or 0) * 60 + float(m.group(2))
        return wait + 1 if wait <= config.LLM_MAX_RATE_LIMIT_WAIT_S else None   # a long pause: fail, do not block the API
    if "429" in text or "rate limit" in text.lower():
        return 2 ** attempt * 5
    return 2 ** attempt                                    # bad JSON, network blip


def _with_retry(call, attempts: int | None = None):
    attempts = attempts or config.LLM_RETRY_ATTEMPTS
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:          # rate limit, network, bad JSON
            last = exc
            wait = _retry_wait(exc, attempt)
            if wait is None or attempt == attempts - 1:
                break
            time.sleep(wait)
    raise RuntimeError(f"LLM call failed after {attempt + 1} attempt(s): {last}") from last


# ---- Ollama (local testing) ---------------------------------------------------

def _ollama_chat(model: str, messages: list[dict[str, Any]], max_tokens: int) -> str:
    body: dict[str, Any] = {
        "model": model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_predict": max_tokens, "num_ctx": config.OLLAMA_NUM_CTX},
        "messages": messages,
    }
    if config.OLLAMA_THINK is not None:          # only sent when set, for models with a thinking mode
        body["think"] = config.OLLAMA_THINK
    req = urllib.request.Request(f"{config.OLLAMA_HOST}/api/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=config.OLLAMA_TIMEOUT_S) as resp:
            return json.loads(resp.read())["message"]["content"]
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Ollama is not reachable at {config.OLLAMA_HOST}: {exc.reason}") from exc


# ---- Groq (hosted) -------------------------------------------------------------

def _groq_chat(model: str, messages: list[dict[str, Any]], max_tokens: int | None = None,
               json_mode: bool = True) -> str:
    kwargs: dict[str, Any] = {"model": model, "temperature": 0, "messages": messages}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if max_tokens:
        kwargs["max_completion_tokens"] = max_tokens
    return _groq().chat.completions.create(**kwargs).choices[0].message.content


# ---- public API -----------------------------------------------------------------

def chat_json(system: str, user: str, model: str | None = None) -> dict[str, Any]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    model_name = model or config.MODEL_NAME

    def call():
        if config.LLM_PROVIDER == "ollama":
            text = _ollama_chat(model_name, messages, max_tokens=config.OLLAMA_MAX_TOKENS_TEXT)
        else:
            text = _groq_chat(model_name, messages)
        return parse_json(text)
    return _with_retry(call)




def _vision_payload(image_path: str | Path) -> tuple[str, str]:
    """Downscale and JPEG-compress before sending. Token cost follows image size."""
    import io
    from PIL import Image

    img = Image.open(image_path).convert("RGB")
    img.thumbnail((config.VISION_MAX_SIDE, config.VISION_MAX_SIDE))   # token cost follows image size
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=config.VISION_JPEG_QUALITY)
    return "image/jpeg", base64.b64encode(buf.getvalue()).decode("utf-8")


def vision_json(image_path: str | Path, prompt: str) -> dict[str, Any]:
    mime, b64 = _vision_payload(image_path)

    def call():
        if config.LLM_PROVIDER == "ollama":
            messages = [{"role": "user", "content": prompt, "images": [b64]}]
            text = _ollama_chat(config.VISION_MODEL_NAME, messages, max_tokens=config.OLLAMA_MAX_TOKENS_VISION)
        else:
            messages = [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ]}]
            text = _groq_chat(config.VISION_MODEL_NAME, messages, max_tokens=config.OLLAMA_MAX_TOKENS_VISION, json_mode=False)
        return parse_json(text)
    return _with_retry(call)
