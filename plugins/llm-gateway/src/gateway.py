from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.assets.run_asset import (
    init_run_asset,
    write_context,
    write_decision,
    write_llm_response,
    write_prompt,
    write_service_asset,
)
from src.clients.query_service import QueryServiceClient
from src.clients.upstream_openai import UpstreamResult, call_openai_compatible
from src.config import Settings
from src.openai_schema import ChatCompletionRequest, extract_system_user
from src.utils.redact import redact_text
from src.utils.time import now_utc_bj


_re_decision_tag = re.compile(r"(?s)<decision>(.*?)</decision>")
_re_json_array = re.compile(r"(?s)\\[\\s*\\{.*?\\}\\s*\\]")
_re_symbol = re.compile(r"^[A-Z0-9]{3,30}$")
_re_thousand_sep = re.compile(r"(?<=\\d),(?=\\d{3})")

_valid_actions = {
    "open_long",
    "open_short",
    "close_long",
    "close_short",
    "hold",
    "wait",
}


@dataclass(frozen=True)
class GatewayResult:
    trace_id: str
    content: str
    decisions: list[dict[str, Any]]
    degraded: bool


def _sanitize_text_for_nofx(s: str) -> str:
    """nofx 的 validateJSONFormat 是对整个 JSON 字符串做 `~`/千分位逗号全局检查。"""
    if not s:
        return ""
    out = s.replace("~", "-").replace("～", "-")
    out = _re_thousand_sep.sub("", out)
    return out


def _redact_dsn_inplace(obj: Any) -> None:
    """Best-effort: avoid leaking internal DSN/topology in run assets and prompts."""
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if str(k).lower() == "dsn":
                obj[k] = None
                continue
            _redact_dsn_inplace(v)
        return
    if isinstance(obj, list):
        for item in obj:
            _redact_dsn_inplace(item)


def _safe_wait(reason: str) -> list[dict[str, Any]]:
    safe_reason = _sanitize_text_for_nofx((reason or "").strip())
    if not safe_reason:
        safe_reason = "safe wait"
    return [{"symbol": "ALL", "action": "wait", "reasoning": safe_reason}]


def _sanitize_decisions_inplace(decisions: list[dict[str, Any]]) -> None:
    for d in decisions:
        for k, v in list(d.items()):
            if isinstance(v, str):
                d[k] = _sanitize_text_for_nofx(v.strip())


def _normalize_decisions(raw: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        d: dict[str, Any] = dict(item)
        if isinstance(d.get("symbol"), str):
            d["symbol"] = d["symbol"].strip().upper()
        if isinstance(d.get("action"), str):
            d["action"] = d["action"].strip().lower()
        if isinstance(d.get("reasoning"), str):
            d["reasoning"] = d["reasoning"].strip()
        out.append(d)
    return out


def _is_finite_positive_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return math.isfinite(float(v)) and float(v) > 0
    return False


def _validate_decisions_schema(decisions: list[dict[str, Any]]) -> str | None:
    if not decisions:
        return "decision must be non-empty list"

    for i, d in enumerate(decisions):
        symbol = d.get("symbol")
        action = d.get("action")
        reasoning = d.get("reasoning")

        if not isinstance(symbol, str) or not symbol.strip():
            return f"decision[{i}].symbol missing"
        symbol = symbol.strip().upper()
        if symbol != "ALL" and not _re_symbol.match(symbol):
            return f"decision[{i}].symbol invalid"

        if not isinstance(action, str) or action not in _valid_actions:
            return f"decision[{i}].action invalid"

        if reasoning is None or not isinstance(reasoning, str):
            return f"decision[{i}].reasoning missing"

        if symbol == "ALL" and action not in {"wait", "hold"}:
            return f"decision[{i}] ALL symbol only allowed for wait/hold"

        if action in {"open_long", "open_short"}:
            if not isinstance(d.get("leverage"), int) or d["leverage"] <= 0:
                return f"decision[{i}].leverage invalid"
            if not _is_finite_positive_number(d.get("position_size_usd")):
                return f"decision[{i}].position_size_usd invalid"
            if not _is_finite_positive_number(d.get("stop_loss")):
                return f"decision[{i}].stop_loss invalid"
            if not _is_finite_positive_number(d.get("take_profit")):
                return f"decision[{i}].take_profit invalid"

        confidence = d.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, int) or confidence < 0 or confidence > 100:
                return f"decision[{i}].confidence invalid"

    return None


def _validate_nofx_json_format(json_str: str) -> str | None:
    s = (json_str or "").strip()
    if not s.startswith("["):
        return "JSON must start with ["
    if not s[1:].lstrip().startswith("{"):
        return "JSON must start with [{"
    if "~" in s:
        return "JSON cannot contain range symbol"
    # thousand separator: digit , digit digit digit
    for i in range(0, max(0, len(s) - 4)):
        if s[i].isdigit() and s[i + 1] == "," and s[i + 2 : i + 5].isdigit():
            return "JSON numbers cannot contain thousand separator comma"
    return None


def _extract_decisions_json(text: str) -> str | None:
    t = (text or "").strip()
    if not t:
        return None
    m = _re_decision_tag.search(t)
    if m:
        return (m.group(1) or "").strip()
    m2 = _re_json_array.search(t)
    if m2:
        return (m2.group(0) or "").strip()
    return None


def _build_reasoning(trace_id: str, upstream: UpstreamResult, degraded: bool, extra: str | None = None) -> str:
    parts = [f"trace_id: {trace_id}", f"degraded: {str(degraded).lower()}"]
    if upstream.error:
        parts.append(f"upstream_error: {upstream.error}")
    if extra:
        parts.append(extra)
    # 不把上游原文 reasoning 全塞进去，避免过长；原文在 llm_response_raw.txt
    return "\n".join(parts).strip()


def _precondition_error(settings: Settings, req: ChatCompletionRequest, system_prompt: str, user_prompt: str) -> str | None:
    if len(req.messages) > settings.MAX_MESSAGES:
        return f"too_many_messages:{len(req.messages)}>{settings.MAX_MESSAGES}"
    if len(system_prompt) > settings.MAX_MESSAGE_CHARS:
        return f"system_prompt_too_long:{len(system_prompt)}>{settings.MAX_MESSAGE_CHARS}"
    if len(user_prompt) > settings.MAX_MESSAGE_CHARS:
        return f"user_prompt_too_long:{len(user_prompt)}>{settings.MAX_MESSAGE_CHARS}"
    return None


async def process_chat_completion(settings: Settings, req: ChatCompletionRequest) -> GatewayResult:
    trace_id = uuid4().hex
    now = now_utc_bj()

    system_prompt_in, user_prompt_in = extract_system_user(req.messages)
    precond_err = _precondition_error(settings, req, system_prompt_in, user_prompt_in)

    paths = init_run_asset(settings.RUN_ASSETS_DIR, trace_id) if settings.RUN_ASSETS_ENABLED else None
    service_index: list[dict[str, Any]] = []
    service_assets: dict[str, dict[str, Any]] = {}

    def _record_service(service: str, out_path: Path) -> None:
        if paths is None:
            return
        try:
            rel = str(out_path.relative_to(paths.root))
        except Exception:
            rel = str(out_path)
        try:
            size = int(out_path.stat().st_size)
        except Exception:
            size = None
        service_index.append(
            {
                "service": service,
                "path": rel,
                "bytes": size,
            }
        )

    degraded = False
    qs_health_dataset: dict[str, Any] | None = None
    qs_caps_dataset: dict[str, Any] | None = None
    sources: list[dict[str, Any]] = []

    def _mk_service_asset(service: str, datasets: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "service_asset_v1",
            "meta": {"trace_id": trace_id, "ts_utc": now.ts_utc, "ts_bj": now.ts_bj},
            "service": service,
            "datasets": datasets,
        }

    if settings.QUERY_SERVICE_BASE_URL:
        qs = QueryServiceClient(
            base_url=settings.QUERY_SERVICE_BASE_URL,
            token=settings.QUERY_SERVICE_TOKEN,
            timeout_seconds=settings.QUERY_SERVICE_TIMEOUT_SECONDS,
        )
        health = await qs.get_health()
        qs_health_dataset = {"ok": health.ok, "error": health.error, "payload": health.data}
        _redact_dsn_inplace(qs_health_dataset)
        if not health.ok:
            degraded = True

        # capabilities 主要用于按 source(id) 决定抓哪些“原材料”
        caps = await qs.get_capabilities()
        qs_caps_dataset = {"ok": caps.ok, "error": caps.error, "payload": caps.data}
        _redact_dsn_inplace(qs_caps_dataset)
        if not caps.ok:
            degraded = True

        # 原材料：按 capabilities.sources 决定有哪些“生产数据的服务”
        if isinstance(qs_caps_dataset.get("payload"), dict):
            raw_sources = qs_caps_dataset["payload"].get("sources")  # type: ignore[union-attr]
            if isinstance(raw_sources, list):
                for s in raw_sources:
                    if not isinstance(s, dict):
                        continue
                    sid = str(s.get("id") or "")
                    sources.append({"id": sid, "ok": bool(s.get("ok"))})

        # market
        market_datasets: dict[str, Any] = {}
        symbols = settings.RAW_SYMBOLS[:20]
        market_ok = any(s.get("id") == "market" and s.get("ok") for s in sources)
        if not market_ok:
            market_datasets["snapshot"] = {"status": "unavailable", "reason": "source_not_ready", "payload": None}
        elif not symbols:
            market_datasets["snapshot"] = {"status": "skipped", "reason": "LLM_GATEWAY_RAW_SYMBOLS empty", "payload": None}
        else:
            snap_items: list[dict[str, Any]] = []
            errors: list[str] = []
            ok_cnt = 0
            for sym in symbols:
                res = await qs.get_symbol_snapshot(
                    sym,
                    panels=(settings.RAW_SNAPSHOT_PANELS or None),
                    intervals=(settings.RAW_SNAPSHOT_INTERVALS or None),
                )
                if res.ok:
                    ok_cnt += 1
                else:
                    errors.append(res.error or "snapshot_error")
                snap_items.append(
                    {
                        "symbol": sym,
                        "ok": res.ok,
                        "error": res.error,
                        "snapshot": res.data if res.ok else None,
                    }
                )
            status = "ok" if ok_cnt == len(symbols) else ("partial" if ok_cnt > 0 else "error")
            market_asset: dict[str, Any] = {
                "status": status,
                "symbols": symbols,
                "panels": settings.RAW_SNAPSHOT_PANELS or None,
                "intervals": settings.RAW_SNAPSHOT_INTERVALS or None,
                "errors": errors or None,
                "payload": snap_items,
            }
            if status != "ok":
                degraded = True
            market_datasets["snapshot"] = market_asset
        if any(s.get("id") == "market" for s in sources):
            service_assets["market"] = _mk_service_asset("market", market_datasets)

        # indicators
        indicators_datasets: dict[str, Any] = {}
        cards = settings.RAW_CARDS[:50]
        indicators_ok = any(s.get("id") == "indicators" and s.get("ok") for s in sources)
        if not indicators_ok:
            indicators_datasets["cards"] = {"status": "unavailable", "reason": "source_not_ready", "payload": None}
        elif not cards:
            indicators_datasets["cards"] = {"status": "skipped", "reason": "LLM_GATEWAY_RAW_CARDS empty", "payload": None}
        else:
            card_items: dict[str, Any] = {}
            card_index: list[dict[str, Any]] = []
            errors: list[str] = []
            ok_cnt = 0
            for cid in cards:
                res = await qs.get_card(
                    cid,
                    interval=settings.RAW_CARD_INTERVAL,
                    limit=settings.RAW_CARD_LIMIT,
                    symbols=(settings.RAW_SYMBOLS[:200] or None),
                )
                if res.ok:
                    ok_cnt += 1
                else:
                    errors.append(res.error or "card_error")
                    degraded = True

                card_items[cid] = {
                    "card_id": cid,
                    "interval": settings.RAW_CARD_INTERVAL,
                    "limit": settings.RAW_CARD_LIMIT,
                    "symbols": (settings.RAW_SYMBOLS[:200] or None),
                    "ok": res.ok,
                    "error": res.error,
                    "payload": res.data if res.ok else None,
                }
                card_index.append({"card_id": cid, "ok": res.ok, "error": res.error})

            status = "ok" if ok_cnt == len(cards) else ("partial" if ok_cnt > 0 else "error")
            indicators_datasets["cards"] = {
                "status": status,
                "interval": settings.RAW_CARD_INTERVAL,
                "limit": settings.RAW_CARD_LIMIT,
                "symbols": (settings.RAW_SYMBOLS[:200] or None),
                "errors": errors or None,
                "index": card_index,
                "items": card_items,
            }
        if any(s.get("id") == "indicators" for s in sources):
            service_assets["indicators"] = _mk_service_asset("indicators", indicators_datasets)

        # unknown sources: still emit 1 JSON per source（不在本次实现范围内的数据用占位）
        for s in sources:
            sid = str(s.get("id") or "").strip()
            if not sid or sid in {"market", "indicators"}:
                continue
            if sid in service_assets:
                continue
            service_assets[sid] = _mk_service_asset(
                sid,
                {
                    "info": {
                        "status": "not_implemented",
                        "reason": "raw collector not implemented yet for this source id",
                        "source_ok": bool(s.get("ok")),
                        "payload": None,
                    }
                },
            )
    else:
        degraded = True
        qs_health_dataset = {"ok": False, "error": "QUERY_SERVICE_BASE_URL not set", "payload": None}
        qs_caps_dataset = {"ok": False, "error": "QUERY_SERVICE_BASE_URL not set", "payload": None}

    # 永远产出 query_service.json（它是“全局目录/索引”的上游来源）
    service_assets["query_service"] = _mk_service_asset(
        "query_service",
        {
            "health": qs_health_dataset,
            "capabilities": qs_caps_dataset,
        },
    )

    # 落盘：每个 service 一个 JSON（用户要求）
    if paths is not None:
        for service, asset in service_assets.items():
            out = write_service_asset(paths, service=service, payload=asset)
            _record_service(service, out)

    # -------------------- context vars（MVP：meta + QueryService health + 原材料索引） --------------------
    context: dict[str, Any] = {
        "schema_version": "v1",
        "meta": {
            "trace_id": trace_id,
            "ts_utc": now.ts_utc,
            "ts_bj": now.ts_bj,
        },
        "nofx_input": {
            "system_prompt": system_prompt_in,
            "user_prompt": user_prompt_in,
        },
        "tradecat": {
            "query_service_ok": bool(qs_health_dataset and qs_health_dataset.get("ok")),
            "query_service_error": (qs_health_dataset or {}).get("error"),
            "query_service_health": (qs_health_dataset or {}).get("payload"),
            "raw_assets": {
                "trace_id": trace_id,
                "run_assets_enabled": bool(paths is not None),
                "services": service_index,
                "constraints": {
                    "symbols": settings.RAW_SYMBOLS[:20] or None,
                    "cards": settings.RAW_CARDS[:50] or None,
                    "card_interval": settings.RAW_CARD_INTERVAL or None,
                    "card_limit": settings.RAW_CARD_LIMIT,
                },
            },
        },
    }

    # context.md（可读版，后续可做精简/长度控制）
    context_md = json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True)
    if len(context_md) > settings.CONTEXT_MD_MAX_CHARS:
        degraded = True
        context["meta"]["truncated"] = True
        context_md = context_md[: settings.CONTEXT_MD_MAX_CHARS] + "\n...<truncated>\n"

    if paths is not None:
        write_context(paths, context, context_md)

    # -------------------- prompt assembly --------------------
    contract = (
        "\n\n"
        "【输出硬契约】\n"
        "1) 必须输出 <reasoning>...</reasoning> 与 <decision>[{...}]</decision>\n"
        "2) <decision> 内必须是 JSON 数组，且必须以 [{ 开头\n"
        "3) 禁止使用 ~（范围值）与千分位逗号（例如 1,234）\n"
        "4) 无法给出有效决策时，必须返回 wait（安全模式）\n"
    )

    if settings.MODE == "passthrough":
        system_prompt = system_prompt_in
        user_prompt = user_prompt_in
    else:
        system_prompt = (system_prompt_in or "").strip() + contract
        user_prompt = (user_prompt_in or "").strip() + "\n\n【TradeCat Context Vars】\n" + context_md

    prompt_text = f"<system>\n{system_prompt}\n</system>\n\n<user>\n{user_prompt}\n</user>\n"
    if paths is not None:
        write_prompt(paths, prompt_text)

    # -------------------- call upstream model --------------------
    model = (req.model or "").strip() or settings.UPSTREAM_MODEL
    max_tokens = req.max_tokens or req.max_completion_tokens

    if precond_err:
        upstream = UpstreamResult(ok=False, content="", raw_json=None, error=f"PRECONDITION_FAILED:{precond_err}")
    else:
        upstream = await call_openai_compatible(
            base_url=settings.UPSTREAM_BASE_URL,
            api_key=settings.UPSTREAM_API_KEY,
            model=model,
            require_https=settings.UPSTREAM_REQUIRE_HTTPS,
            block_private=settings.UPSTREAM_BLOCK_PRIVATE,
            allowlist_raw=settings.UPSTREAM_ALLOWLIST,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=req.temperature,
            max_tokens=max_tokens,
            timeout_seconds=settings.UPSTREAM_TIMEOUT_SECONDS,
        )

    # 记录上游原始输出（脱敏）
    if paths is not None:
        if upstream.ok:
            write_llm_response(paths, upstream.content)
        elif upstream.raw_json is not None:
            write_llm_response(paths, redact_text(json.dumps(upstream.raw_json, ensure_ascii=False)))
        else:
            write_llm_response(paths, upstream.error or "")

    decisions: list[dict[str, Any]]
    if precond_err:
        degraded = True
        decisions = _safe_wait(f"precondition failed: {precond_err}; safe wait")
    else:
        decisions_json = _extract_decisions_json(upstream.content) if upstream.ok else None
        if not decisions_json:
            degraded = True
            decisions = _safe_wait("upstream did not return structured JSON decision; safe wait")
        else:
            try:
                parsed = json.loads(decisions_json)
                if not isinstance(parsed, list):
                    raise ValueError("decision must be list")
                normalized = _normalize_decisions(parsed)
                _sanitize_decisions_inplace(normalized)
                schema_err = _validate_decisions_schema(normalized)
                if schema_err:
                    raise ValueError(schema_err)

                rendered = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
                fmt_err = _validate_nofx_json_format(rendered)
                if fmt_err:
                    raise ValueError(fmt_err)

                decisions = normalized
            except Exception as e:
                degraded = True
                decisions = _safe_wait(f"decision invalid; safe wait ({type(e).__name__})")

    # 保存结构化决策
    if paths is not None:
        write_decision(paths, decisions)

    # 最终回传给 nofx 的 content：始终带 <decision>，保证 nofx 不报解析错误
    reasoning = _build_reasoning(trace_id, upstream, degraded)
    decision_str = json.dumps(decisions, ensure_ascii=False, separators=(",", ":"))
    content = f"<reasoning>\n{reasoning}\n</reasoning>\n<decision>\n{decision_str}\n</decision>\n"

    return GatewayResult(trace_id=trace_id, content=content, decisions=decisions, degraded=degraded)
