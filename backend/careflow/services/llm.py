"""Local Ollama client. Backend-only, configurable endpoint, strict JSON via schema-constrained output.
There is deliberately NO cloud fallback: on failure we raise LLMUnavailable and callers degrade safely."""
import json
import time
from typing import Any

import httpx

from ..config import get_settings


class LLMUnavailable(Exception):
    """Ollama down, model missing, timed out, or returned unusable output."""


def status() -> dict:
    s = get_settings()
    if not s.llm_enabled:
        return {"available": False, "model": s.ollama_model, "detail": "LLM disabled by configuration"}
    try:
        r = httpx.get(f"{s.ollama_base_url}/api/tags", timeout=3.0)
        r.raise_for_status()
        names = [m.get("name") for m in r.json().get("models", [])]
        ok = s.ollama_model in names or any(n and n.split(":")[0] == s.ollama_model and ":" not in s.ollama_model for n in names)
        return {"available": ok, "model": s.ollama_model, "installed_models": names,
                "detail": "ready" if ok else f"model '{s.ollama_model}' is not installed (run: ollama pull {s.ollama_model})"}
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "model": s.ollama_model, "detail": f"Ollama unreachable at {s.ollama_base_url}: {type(exc).__name__}"}


def chat_json(system: str, user: str, schema: dict, *, num_predict: int = 1500) -> tuple[dict, dict]:
    """Returns (parsed_json, meta). Raises LLMUnavailable on any failure; caller validates content."""
    s = get_settings()
    if not s.llm_enabled:
        raise LLMUnavailable("LLM disabled by configuration")
    body = {
        "model": s.ollama_model, "stream": False, "think": False, "format": schema,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": num_predict, "seed": 7},
    }
    t0 = time.monotonic()
    try:
        r = httpx.post(f"{s.ollama_base_url}/api/chat", json=body, timeout=s.ollama_timeout_seconds)
        if r.status_code == 404:
            raise LLMUnavailable(f"model '{s.ollama_model}' not installed")
        r.raise_for_status()
        raw = r.json()["message"]["content"]
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise LLMUnavailable("model returned non-object JSON")
    except LLMUnavailable:
        raise
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(f"malformed JSON from model: {exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001  (timeouts, connection errors, HTTP errors)
        raise LLMUnavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
    return parsed, {"model": s.ollama_model, "latency_ms": int((time.monotonic() - t0) * 1000), "raw": raw}
