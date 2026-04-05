from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from src.utils.net import validate_upstream_url

LOG = logging.getLogger("tradecat.llm_gateway.upstream")


@dataclass(frozen=True)
class UpstreamResult:
    ok: bool
    content: str
    raw_json: dict | None = None
    error: str | None = None


def _build_url(base_url: str) -> tuple[str, bool]:
    """沿用 nofx 的约定：末尾 `#` 表示 full URL，否则追加 `/chat/completions`。"""
    base = (base_url or "").strip()
    if not base:
        return ("", False)
    if base.endswith("#"):
        return (base[:-1], True)
    return (base.rstrip("/") + "/chat/completions", True)


async def call_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    require_https: bool,
    block_private: bool,
    allowlist_raw: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float | None,
    max_tokens: int | None,
    timeout_seconds: float,
) -> UpstreamResult:
    url, ok = _build_url(base_url)
    if not ok:
        return UpstreamResult(ok=False, content="", raw_json=None, error="UPSTREAM_BASE_URL not set")
    if not api_key:
        return UpstreamResult(ok=False, content="", raw_json=None, error="UPSTREAM_API_KEY not set")
    if not model:
        return UpstreamResult(ok=False, content="", raw_json=None, error="UPSTREAM_MODEL not set")

    url_err = validate_upstream_url(
        url,
        require_https=require_https,
        block_private=block_private,
        allowlist_raw=allowlist_raw,
    )
    if url_err:
        return UpstreamResult(ok=False, content="", raw_json=None, error=f"UNSAFE_UPSTREAM_URL:{url_err}")

    req: dict = {
        "model": model,
        "messages": [],
    }
    if system_prompt:
        req["messages"].append({"role": "system", "content": system_prompt})
    req["messages"].append({"role": "user", "content": user_prompt})
    if temperature is not None:
        req["temperature"] = temperature
    if max_tokens is not None:
        req["max_tokens"] = max_tokens

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client:
            r = await client.post(url, json=req, headers=headers)
            raw = r.json() if r.content else {}
            if r.status_code != 200:
                return UpstreamResult(ok=False, content="", raw_json=raw, error=f"upstream_status={r.status_code}")
            content = (((raw.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
            return UpstreamResult(ok=True, content=content, raw_json=raw)
    except Exception as e:
        LOG.warning("upstream_call_failed url=%s err=%s", url, e)
        return UpstreamResult(ok=False, content="", raw_json=None, error=str(e))
