"""FastAPI 应用：nofx 外部提示词组装/决策网关（OpenAI-compatible）"""

from __future__ import annotations

import json
import logging
import time

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from src import __version__
from src.config import get_settings
from src.gateway import GatewayResult, process_chat_completion
from src.middleware.chat_guard import ChatCompletionsGuardMiddleware
from src.openai_response import build_chat_completion_response
from src.openai_schema import ChatCompletionRequest


LOG = logging.getLogger("tradecat.llm_gateway")


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    s = authorization.strip()
    if not s.lower().startswith("bearer "):
        return ""
    return s[7:].strip()


app = FastAPI(
    title="TradeCat LLM Gateway",
    description="nofx 外部提示词组装/决策网关（OpenAI-compatible）",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 安全门禁：在 FastAPI 解析 body 前做鉴权与 body size 限制（避免 DoS 与多世界）
app.add_middleware(ChatCompletionsGuardMiddleware)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """nofx 侧遇到非 200 会直接报错，因此：鉴权通过后，尽量用 200 + 安全决策兜底。"""
    settings = get_settings()
    auth = _bearer_token(request.headers.get("authorization"))
    if not settings.TOKEN or auth != settings.TOKEN:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    msg = "参数校验失败，进入安全 wait"
    decision = [{"symbol": "ALL", "action": "wait", "reasoning": msg}]
    content = (
        "<reasoning>\n" + msg + "\n</reasoning>\n<decision>\n" + json.dumps(decision, ensure_ascii=False) + "\n</decision>\n"
    )
    return JSONResponse(status_code=200, content=build_chat_completion_response(content, model=None))


@app.get("/healthz")
async def healthz() -> dict:
    settings = get_settings()
    upstream_configured = bool(settings.UPSTREAM_BASE_URL and settings.UPSTREAM_API_KEY and (settings.UPSTREAM_MODEL))
    return {
        "status": "ok",
        "service": "llm-gateway-service",
        "version": __version__,
        "limits": {
            "max_body_bytes": settings.MAX_BODY_BYTES,
            "max_messages": settings.MAX_MESSAGES,
            "max_message_chars": settings.MAX_MESSAGE_CHARS,
        },
        "run_assets": {
            "enabled": settings.RUN_ASSETS_ENABLED,
        },
        "query_service": {
            "configured": bool(settings.QUERY_SERVICE_BASE_URL),
        },
        "upstream": {
            "configured": upstream_configured,
            "require_https": settings.UPSTREAM_REQUIRE_HTTPS,
            "block_private": settings.UPSTREAM_BLOCK_PRIVATE,
            "allowlist_set": bool(settings.UPSTREAM_ALLOWLIST),
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> dict:
    settings = get_settings()
    token = _bearer_token(authorization)
    if not settings.TOKEN or token != settings.TOKEN:
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    start = time.perf_counter()
    try:
        result: GatewayResult = await process_chat_completion(settings, req)
        duration_ms = int((time.perf_counter() - start) * 1000)
        LOG.info(
            "chat_completions_ok trace_id=%s degraded=%s duration_ms=%d",
            result.trace_id,
            str(result.degraded).lower(),
            duration_ms,
        )
        return build_chat_completion_response(result.content, model=req.model)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        LOG.error("chat_completions_failed", exc_info=True)
        LOG.error("chat_completions_failed_meta duration_ms=%d", duration_ms)
        msg = "网关内部错误，进入安全 wait"
        decision = [{"symbol": "ALL", "action": "wait", "reasoning": msg}]
        content = (
            "<reasoning>\n" + msg + "\n</reasoning>\n<decision>\n" + json.dumps(decision, ensure_ascii=False) + "\n</decision>\n"
        )
        return build_chat_completion_response(content, model=req.model)
