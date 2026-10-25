"""Local Ollama client. Backend-only, configurable endpoint, strict JSON via schema-constrained output.
Supports two distinct local model stages:
- Stage 1 (`ollama_model`, default `qwen3:4b`): structured fact extraction and translation.
- Stage 2 (`medgemma_model`, default `medgemma1.5`): medical evidence analysis, synthesis, and adaptive follow-up refinement.
Calls are serialised with a lock so both models are never loaded concurrently in a way that exhausts RAM/VRAM.
There is deliberately NO cloud fallback: on failure we raise LLMUnavailable and callers degrade safely."""
import json
import re
import threading
import time
from typing import Optional

import httpx

from ..config import get_settings

_infer_lock = threading.Lock()


class LLMUnavailable(Exception):
    """Ollama down, model missing, timed out, or returned unusable output."""


def _matches_installed(target: str, names: list[str]) -> bool:
    if not target:
        return False
    t_low = target.lower()
    base = t_low.split(":")[0]
    for n in names:
        if not n:
            continue
        nl = n.lower()
        if nl == t_low or nl.split(":")[0] == base or base.replace("1.5", "") in nl:
            return True
    return False


def resolve_model_name(target: str, installed: list[str]) -> str:
    """Resolve configured model alias (e.g. 'medgemma1.5') to exact installed tag if present."""
    if not installed or target in installed:
        return target
    t_low = target.lower()
    base = t_low.split(":")[0]
    for n in installed:
        if n and (n.lower() == t_low or n.lower().split(":")[0] == base):
            return n
    for n in installed:
        if n and "medgemma" in t_low and "medgemma" in n.lower():
            return n
    return target


def status() -> dict:
    s = get_settings()
    if not s.llm_enabled:
        return {
            "available": False,
            "model": s.ollama_model,
            "extraction_model": s.ollama_model,
            "analysis_model": s.medgemma_model,
            "medgemma_available": False,
            "detail": "LLM disabled by configuration",
        }
    try:
        r = httpx.get(f"{s.ollama_base_url}/api/tags", timeout=3.0)
        r.raise_for_status()
        names = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
        ok_extract = _matches_installed(s.ollama_model, names)
        ok_medgemma = _matches_installed(s.medgemma_model, names)
        detail_parts = []
        if ok_extract:
            detail_parts.append(f"extraction ({s.ollama_model}): ready")
        else:
            detail_parts.append(f"model '{s.ollama_model}' is not installed (run: ollama pull {s.ollama_model})")
        if ok_medgemma:
            detail_parts.append(f"analysis ({s.medgemma_model}): ready")
        else:
            detail_parts.append(f"analysis model '{s.medgemma_model}' not installed (run: ollama pull {s.medgemma_model})")
        return {
            "available": ok_extract,
            "model": s.ollama_model,
            "extraction_model": s.ollama_model,
            "analysis_model": s.medgemma_model,
            "medgemma_available": ok_medgemma,
            "installed_models": names,
            "detail": "ready" if (ok_extract and ok_medgemma) else "; ".join(detail_parts),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "model": s.ollama_model,
            "extraction_model": s.ollama_model,
            "analysis_model": s.medgemma_model,
            "medgemma_available": False,
            "detail": f"Ollama unreachable at {s.ollama_base_url}: {type(exc).__name__}",
        }


def _strip_think(raw: str) -> str:
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL).strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I).strip()
    return cleaned


def chat_json(
    system: str,
    user: str,
    schema: dict,
    *,
    num_predict: int = 1500,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
) -> tuple[dict, dict]:
    """Returns (parsed_json, meta). Raises LLMUnavailable on any failure; caller validates content."""
    s = get_settings()
    if not s.llm_enabled:
        raise LLMUnavailable("LLM disabled by configuration")
    target_model = model or s.ollama_model
    req_timeout = timeout if timeout is not None else (
        s.medgemma_timeout_seconds if target_model == s.medgemma_model else s.ollama_timeout_seconds
    )
    body = {
        "model": target_model,
        "stream": False,
        "think": False,
        "keep_alive": s.ollama_keep_alive,
        "format": schema,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user[:16000]}],
        "options": {"temperature": 0, "num_ctx": 4096, "num_predict": num_predict, "seed": 7},
    }
    t0 = time.monotonic()
    with _infer_lock:
        try:
            r = httpx.post(f"{s.ollama_base_url}/api/chat", json=body, timeout=req_timeout)
            if r.status_code == 404:
                raise LLMUnavailable(f"model '{target_model}' not installed")
            r.raise_for_status()
            raw = r.json().get("message", {}).get("content", "")
            cleaned = _strip_think(raw)
            parsed = json.loads(cleaned)
            if not isinstance(parsed, dict):
                raise LLMUnavailable("model returned non-object JSON")
            req_keys = schema.get("required") or []
            missing_keys = [k for k in req_keys if k not in parsed]
            if missing_keys:
                raise LLMUnavailable(f"model response missing required schema keys: {', '.join(missing_keys)}")
        except LLMUnavailable:
            raise
        except json.JSONDecodeError as exc:
            raise LLMUnavailable(f"malformed JSON from model '{target_model}': {exc.msg}") from exc
        except Exception as exc:  # noqa: BLE001  (timeouts, connection errors, HTTP errors)
            raise LLMUnavailable(f"{type(exc).__name__}: {str(exc)[:160]}") from exc
    return parsed, {"model": target_model, "latency_ms": int((time.monotonic() - t0) * 1000), "raw": raw}
