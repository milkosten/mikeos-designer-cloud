"""GPU client for the shared free Ollama (qwen3:8b) at OLLAMA_GPU_URL.

Parses `ollama://user:pass@host:port` into base URL + HTTP-Basic header, POSTs to
`https://<host>:<port>/api/chat` with the self-signed cert unverified, `think:false`
(qwen3 returns empty otherwise), `stream:false`, and optional `format` (a JSON schema)
for the plan step.

The GPU is a SHARED single card (~12 GB, one model resident): we SERIALIZE calls (one
in flight across the whole service) and back off on 503 ("max pending requests" / model
still loading), so we never thrash the queue. Reference client:
mikeos-photos-cloud/server/analysis/vision.py.
"""
import asyncio
import base64
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

OLLAMA_GPU_URL = os.environ.get(
    "OLLAMA_GPU_URL",
    "ollama://mikeos:uB49VXwMDy7R2JE0H7mI@81.8.177.182:11443",
)
TEXT_MODEL = os.environ.get("OLLAMA_TEXT_MODEL", "qwen3:8b")

# Only ONE GPU request in flight across the whole service.
_gpu_sem = asyncio.Semaphore(1)


def _endpoint() -> tuple[str, Dict[str, str]]:
    """Parse OLLAMA_GPU_URL (ollama://user:pass@host:port) -> (base_url, headers)."""
    raw = OLLAMA_GPU_URL
    scheme = "https"
    rest = raw
    if "://" in raw:
        s, rest = raw.split("://", 1)
        s = s.lower()
        scheme = "http" if s in ("ollama+http", "http") else "https"
    headers: Dict[str, str] = {}
    if "@" in rest:
        creds, rest = rest.rsplit("@", 1)
        headers["Authorization"] = "Basic " + base64.b64encode(creds.encode()).decode()
    return f"{scheme}://{rest.rstrip('/')}", headers


async def chat(
    messages: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    schema: Optional[Dict[str, Any]] = None,
    temperature: float = 0.7,
    num_ctx: int = 8192,
    num_predict: int = 6144,
    timeout: float = 420.0,
    keep_alive: str = "30m",
    max_retries: int = 4,
) -> str:
    """One serialized Ollama /api/chat call. `schema` constrains output to JSON
    (the plan step). Retries with exponential backoff on 503 (GPU loading / queue full)
    and transient network errors. Returns the assistant message content."""
    base, headers = _endpoint()
    body: Dict[str, Any] = {
        "model": model or TEXT_MODEL,
        "messages": messages,
        "stream": False,
        "think": False,  # qwen3 returns empty without this
        "keep_alive": keep_alive,
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    if schema is not None:
        body["format"] = schema

    last_err: Optional[Exception] = None
    async with _gpu_sem:
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=timeout, verify=False) as client:
                    resp = await client.post(f"{base}/api/chat", json=body, headers=headers)
                if resp.status_code == 503:
                    # GPU is loading a model / queue is full — back off and retry.
                    wait = min(60.0, 5.0 * (2 ** attempt))
                    logger.warning("GPU 503 (loading/queue full), retry in %.0fs", wait)
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()
                data = resp.json()
                content = (data.get("message") or {}).get("content", "")
                if not content.strip():
                    last_err = RuntimeError("GPU returned empty content")
                    await asyncio.sleep(min(30.0, 3.0 * (2 ** attempt)))
                    continue
                return content
            except httpx.HTTPStatusError as e:
                last_err = e
                logger.warning("GPU HTTP %s (attempt %d)", e.response.status_code, attempt + 1)
                await asyncio.sleep(min(30.0, 3.0 * (2 ** attempt)))
            except Exception as e:  # network / timeout
                last_err = e
                logger.warning("GPU call failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(min(30.0, 3.0 * (2 ** attempt)))

    raise RuntimeError(f"GPU call failed after {max_retries} attempts: {last_err}")
