from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

LOG = logging.getLogger("tradecat.llm_gateway.query_service")


@dataclass(frozen=True)
class QueryServiceResult:
    ok: bool
    data: Any | None
    error: str | None = None
    upstream: dict[str, Any] | None = None
    http_status: int | None = None


class QueryServiceClient:
    def __init__(self, base_url: str, token: str, timeout_seconds: float):
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._token:
            headers["X-Internal-Token"] = self._token
        return headers

    async def _get(self, path: str, *, query: dict[str, Any] | None = None) -> QueryServiceResult:
        if not self._base_url:
            return QueryServiceResult(ok=False, data=None, error="QUERY_SERVICE_BASE_URL not set")
        p = path if path.startswith("/") else f"/{path}"
        url = f"{self._base_url}{p}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=False) as client:
                r = await client.get(url, headers=self._headers(), params=query)
                payload: Any = r.json() if r.content else {}
                http_status = int(r.status_code)
        except Exception as e:
            LOG.warning("query_service_health_failed url=%s err=%s", url, e)
            return QueryServiceResult(ok=False, data=None, error=str(e), upstream=None, http_status=None)

        upstream = payload if isinstance(payload, dict) else None
        if http_status != 200:
            return QueryServiceResult(
                ok=False,
                data=None,
                error=f"http_status={http_status}",
                upstream=upstream,
                http_status=http_status,
            )

        # Query Service v1: HTTP 200 with {success, code, msg, data}
        if isinstance(payload, dict) and payload.get("success") is True:
            return QueryServiceResult(ok=True, data=payload.get("data"), error=None, upstream=upstream, http_status=http_status)

        code = str(payload.get("code") or "") if isinstance(payload, dict) else ""
        msg = str(payload.get("msg") or "") if isinstance(payload, dict) else ""
        if msg == "unauthorized":
            return QueryServiceResult(ok=False, data=None, error=f"unauthorized:{code}", upstream=upstream, http_status=http_status)

        err = f"upstream_error:{code}:{msg}" if (code or msg) else "upstream_error:unknown"
        return QueryServiceResult(ok=False, data=None, error=err, upstream=upstream, http_status=http_status)

    async def get_health(self) -> QueryServiceResult:
        return await self._get("/api/v1/health")

    async def get_capabilities(self) -> QueryServiceResult:
        return await self._get("/api/v1/capabilities")

    async def get_symbol_snapshot(
        self,
        symbol: str,
        *,
        panels: list[str] | None = None,
        intervals: list[str] | None = None,
    ) -> QueryServiceResult:
        sym = (symbol or "").strip()
        query: dict[str, Any] = {}
        if panels:
            query["panels"] = ",".join([p for p in panels if p])
        if intervals:
            query["intervals"] = ",".join([i for i in intervals if i])
        return await self._get(f"/api/v1/symbol/{sym}/snapshot", query=query or None)

    async def get_card(
        self,
        card_id: str,
        *,
        interval: str,
        limit: int,
        symbols: list[str] | None = None,
    ) -> QueryServiceResult:
        cid = (card_id or "").strip()
        query: dict[str, Any] = {"interval": (interval or "").strip(), "limit": int(limit)}
        if symbols:
            query["symbols"] = ",".join([s for s in symbols if s])
        return await self._get(f"/api/v1/cards/{cid}", query=query)
