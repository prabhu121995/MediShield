"""
LLM helpers: JSON chat, and JSON from a vision model reading an image.

The provider comes from LLM_PROVIDER in .env:
- "gemini": Google Gemini through the Generative Language REST API (GEMINI_API_KEY). A model
  that is rate limited or overloaded hands the call to the next one in GEMINI_FALLBACK_MODELS.
- "groq": a hosted model through the Groq API.
- "ollama": a local model through the Ollama HTTP API. Slow on CPU; for offline testing only.

Every call retries with backoff, so a rate limit or a bad JSON reply does not crash the case.
Every call can be cancelled: the request runs on its own thread and the caller polls the
cancel hook (set by the claim graph), so stopping a claim does not wait for a slow model.
Every call is a LangSmith "llm" run (when LANGSMITH_TRACING is on), nested under the agent
node that made it.
"""

from __future__ import annotations

import base64
import contextvars
import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from langsmith import traceable

from app import config

_client = None

# Raises when the work this call belongs to was stopped. Set by the claim graph (app.claim._node).
_cancel_hook: Callable[[], None] = lambda: None
_POLL_S = 0.5


def set_cancel_hook(hook: Callable[[], None]) -> None:
    global _cancel_hook
    _cancel_hook = hook


def _cancellable(call):
    """Run call() on its own thread; raise the cancel hook's exception as soon as it fires.
    A cancelled request is abandoned: its reply, when it comes, is dropped."""
    _cancel_hook()
    box: dict[str, Any] = {}

    def work():
        try:
            box["value"] = call()
        except BaseException as exc:      # handed to the caller below
            box["error"] = exc
    t = threading.Thread(target=contextvars.copy_context().run, args=(work,), name="llm-call", daemon=True)
    t.start()
    while t.is_alive():
        t.join(_POLL_S)
        _cancel_hook()
    if "error" in box:
        raise box["error"]
    return box["value"]


def _sleep(seconds: float) -> None:
    end = time.monotonic() + seconds
    while (left := end - time.monotonic()) > 0:
        _cancel_hook()
        time.sleep(min(_POLL_S, left))


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


_RETRY_AFTER = re.compile(r"(?:try again|retry) in (?:(\d+)m)?([\d.]+)s")


def _retry_wait(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if waiting cannot help."""
    text = str(exc)
    m = _RETRY_AFTER.search(text)
    if m:
        wait = int(m.group(1) or 0) * 60 + float(m.group(2))
        return wait + 1 if wait <= config.LLM_MAX_RATE_LIMIT_WAIT_S else None   # a long pause: fail, do not block the API
    if "429" in text or "rate limit" in text.lower() or "503" in text:
        return 2 ** attempt * 5
    return 2 ** attempt                                    # bad JSON, network blip


def _with_retry(call, attempts: int | None = None):
    attempts = attempts or config.LLM_RETRY_ATTEMPTS
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return _cancellable(call)
        except Exception as exc:          # rate limit, network, bad JSON
            last = exc
            wait = _retry_wait(exc, attempt)
            if wait is None or attempt == attempts - 1:
                break
            _sleep(wait)
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


# ---- Gemini (hosted) -------------------------------------------------------------

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_GEMINI_RETRY_DELAY = re.compile(r'"retryDelay":\s*"([\d.]+)s"')


class GeminiBusy(RuntimeError):
    """This model is rate limited or overloaded; the next model in the chain may not be."""


def _gemini_once(model: str, system: str | None, parts: list[dict[str, Any]]) -> str:
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set in .env")
    gen: dict[str, Any] = {"temperature": 0, "responseMimeType": "application/json"}
    if config.GEMINI_THINKING_LEVEL and not model.startswith("gemini-2."):   # 2.x models take a budget, not a level
        gen["thinkingConfig"] = {"thinkingLevel": config.GEMINI_THINKING_LEVEL}
    body: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}], "generationConfig": gen}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    req = urllib.request.Request(_GEMINI_URL.format(model=model), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=config.GEMINI_TIMEOUT_S or None) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", "replace")
        try:
            message = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = text[:300]
        delay = _GEMINI_RETRY_DELAY.search(text)
        hint = f" (retry in {delay.group(1)}s)" if delay else ""
        err = f"Gemini {model} HTTP {exc.code}: {message[:300]}{hint}"
        raise (GeminiBusy if exc.code in (429, 500, 503, 504) else RuntimeError)(err) from exc
    except urllib.error.URLError as exc:
        raise GeminiBusy(f"Gemini {model} not reachable: {exc.reason}") from exc
    candidate = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (candidate.get("content") or {}).get("parts") or []
                   if not p.get("thought"))
    if not text:
        raise RuntimeError(f"Gemini {model} returned no text (finish reason {candidate.get('finishReason')})")
    return text


def _gemini(model: str, system: str | None, parts: list[dict[str, Any]]) -> str:
    """The model first, then each fallback, until one is not rate limited or overloaded."""
    chain = [model] + [m for m in config.GEMINI_FALLBACK_MODELS if m != model]
    busy: list[str] = []
    for m in chain:
        _cancel_hook()
        try:
            return _gemini_once(m, system, parts)
        except GeminiBusy as exc:
            busy.append(str(exc))
    raise RuntimeError(" | ".join(busy))


# ---- public API -----------------------------------------------------------------

@traceable(run_type="llm", name="chat_json")
def chat_json(system: str, user: str, model: str | None = None) -> dict[str, Any]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    model_name = model or config.MODEL_NAME

    def call():
        if config.LLM_PROVIDER == "gemini":
            text = _gemini(model_name, system, [{"text": user}])
        elif config.LLM_PROVIDER == "ollama":
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


@traceable(run_type="llm", name="vision_json")
def vision_json(image_path: str | Path, prompt: str) -> dict[str, Any]:
    mime, b64 = _vision_payload(image_path)

    def call():
        if config.LLM_PROVIDER == "gemini":
            text = _gemini(config.VISION_MODEL_NAME, None,
                           [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}])
        elif config.LLM_PROVIDER == "ollama":
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
