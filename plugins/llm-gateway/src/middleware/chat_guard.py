from __future__ import annotations

import json
from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from src.config import get_settings
from src.openai_response import build_chat_completion_response


class _BodyTooLarge(Exception):
    pass


def _bearer_token_from_scope(scope: Scope) -> str:
    headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
    raw = headers.get(b"authorization")
    if not raw:
        return ""
    s = raw.decode("utf-8", errors="ignore").strip()
    if not s.lower().startswith("bearer "):
        return ""
    return s[7:].strip()


def _safe_wait_content(msg: str, trace_id: str) -> str:
    decision = [{"symbol": "ALL", "action": "wait", "reasoning": msg}]
    return (
        "<reasoning>\n"
        + f"trace_id: {trace_id}\n"
        + msg
        + "\n</reasoning>\n<decision>\n"
        + json.dumps(decision, ensure_ascii=False, separators=(",", ":"))
        + "\n</decision>\n"
    )


class ChatCompletionsGuardMiddleware:
    """在 FastAPI 解析 body 之前做 fail-fast，避免 unauthorized/超大 body 触发 DoS 与多世界。"""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        token = _bearer_token_from_scope(scope)
        if not settings.TOKEN or token != settings.TOKEN:
            await JSONResponse(status_code=401, content={"error": "unauthorized"})(scope, receive, send)
            return

        trace_id = uuid4().hex
        max_body = max(0, int(settings.MAX_BODY_BYTES))

        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                if int(content_length.decode("utf-8", errors="ignore")) > max_body:
                    msg = f"请求体过大(>{max_body} bytes)，进入安全 wait"
                    content = _safe_wait_content(msg, trace_id=trace_id)
                    await JSONResponse(status_code=200, content=build_chat_completion_response(content, model=None))(scope, receive, send)
                    return
            except ValueError:
                # 非法 content-length：按“最坏情况”处理，进入安全 wait
                msg = "Content-Length 非法，进入安全 wait"
                content = _safe_wait_content(msg, trace_id=trace_id)
                await JSONResponse(status_code=200, content=build_chat_completion_response(content, model=None))(scope, receive, send)
                return

        received = 0

        async def receive_limited() -> dict:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"") or b""
                received += len(chunk)
                if received > max_body:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, receive_limited, send)
        except _BodyTooLarge:
            msg = f"请求体过大(>{max_body} bytes)，进入安全 wait"
            content = _safe_wait_content(msg, trace_id=trace_id)
            await JSONResponse(status_code=200, content=build_chat_completion_response(content, model=None))(scope, receive, send)

