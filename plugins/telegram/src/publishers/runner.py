from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from telegram import Bot
from telegram.error import NetworkError, RetryAfter, TimedOut

try:
    from path_setup import ensure_runtime_sys_path, get_service_paths, load_shared_env  # type: ignore
except Exception:  # pragma: no cover
    from src.path_setup import ensure_runtime_sys_path, get_service_paths, load_shared_env  # type: ignore


SERVICE_PATHS = get_service_paths()
ensure_runtime_sys_path()
load_shared_env()

UTC = timezone.utc
LOG = logging.getLogger(__name__)
QUERY_TIMEOUT = float(os.getenv("QUERY_SERVICE_TIMEOUT_SECONDS", "6") or "6")


def _service_paths() -> tuple[Path, Path]:
    base = SERVICE_PATHS.service_root
    data_dir = base / "data" / "publishers"
    data_dir.mkdir(parents=True, exist_ok=True)
    (base / "logs").mkdir(parents=True, exist_ok=True)
    return base, data_dir


def _env_first(*keys: str) -> str:
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def _env_csv(*keys: str) -> list[str]:
    raw = _env_first(*keys)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _unwrap_envelope(payload: dict[str, Any]) -> Any:
    if payload.get("success") is True:
        return payload.get("data")
    if int(payload.get("code") or 0) == 0:
        return payload.get("data")
    raise RuntimeError(payload.get("message") or "query_service_error")


def _parse_iso(ts_raw: str | None) -> datetime:
    raw = (ts_raw or "").strip()
    if not raw:
        return datetime(1970, 1, 1, tzinfo=UTC)
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


class QueryEventsClient:
    def __init__(self) -> None:
        self.base_url = (_env_first("QUERY_SERVICE_BASE_URL") or "http://127.0.0.1:8088").rstrip("/")
        self.token = _env_first("QUERY_SERVICE_TOKEN")
        self.auth_mode = (_env_first("QUERY_SERVICE_AUTH_MODE") or "required").lower()

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.auth_mode not in {"disabled", "off"}:
            if not self.token:
                raise RuntimeError("QUERY_SERVICE_TOKEN 未配置")
            headers["X-Internal-Token"] = self.token
        return headers

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        with httpx.Client(timeout=QUERY_TIMEOUT, headers=self._headers()) as client:
            response = client.get(url, params=params)
            response.raise_for_status()
            return _unwrap_envelope(response.json())

    def latest_cursor(self) -> tuple[datetime, str]:
        data = self._get("/api/v1/events/latest")
        return _parse_iso(data.get("ingested_at")), str(data.get("event_hash") or "")

    def fetch_events(self, *, since_ts: datetime, since_hash: str, limit: int = 100) -> list[dict[str, Any]]:
        data = self._get(
            "/api/v1/events",
            params={
                "since_ts": _iso_z(since_ts),
                "since_hash": since_hash,
                "limit": int(limit),
                "offset": 0,
            },
        )
        rows = data.get("list") or []
        return [item for item in rows if isinstance(item, dict)]


class BotPool:
    def __init__(self, tokens: list[str]) -> None:
        self.bots = [Bot(token=token) for token in tokens]
        self.index = 0
        self.cooldowns = {idx: 0.0 for idx in range(len(tokens))}

    def get(self) -> tuple[int, Bot]:
        now = time.time()
        for _ in range(len(self.bots)):
            idx = self.index
            self.index = (self.index + 1) % len(self.bots)
            if now >= self.cooldowns[idx]:
                return idx, self.bots[idx]
        idx = min(self.cooldowns, key=self.cooldowns.get)
        wait = self.cooldowns[idx] - now
        if wait > 0:
            time.sleep(wait)
        return idx, self.bots[idx]

    def cooldown(self, idx: int, seconds: float) -> None:
        self.cooldowns[idx] = time.time() + seconds


@dataclass(frozen=True)
class PublishMode:
    name: str
    channel_id: str
    bot_tokens: list[str]
    poll_interval: float
    include_tags: set[str]
    exclude_tags: set[str]
    cursor_file: Path
    format_message: Callable[[dict[str, Any]], str]


def _format_json_event(event: dict[str, Any]) -> str:
    return json.dumps(
        {
            "type": event.get("type"),
            "ts": event.get("ts"),
            "ingested_at": event.get("ingested_at"),
            "event_hash": event.get("event_hash"),
            "tag": event.get("tag"),
            "source": event.get("source"),
            "content": (event.get("content") or "")[:3000],
        },
        ensure_ascii=False,
    )


def _format_signal_event(event: dict[str, Any]) -> str:
    ts_raw = str(event.get("ts") or "")
    ts_text = ts_raw[11:16] if len(ts_raw) >= 16 else ts_raw
    tag = str(event.get("tag") or "")
    source = str(event.get("source") or "")
    parts = []
    if tag:
        parts.append(f"[{tag}]")
    if ts_text:
        parts.append(ts_text)
    if source:
        parts.append(f"@{source}")
    header = " ".join(parts).strip()
    content = (event.get("content") or "")[:3500]
    return f"{header}\n{content}".strip() if header else content


def _load_cursor(path: Path) -> tuple[datetime, str] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _parse_iso(payload.get("ingested_at")), str(payload.get("event_hash") or "")
    except Exception as exc:
        LOG.warning("读取游标失败，将重新初始化: %s", exc)
        return None


def _save_cursor(path: Path, *, ingested_at: datetime, event_hash: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"ingested_at": _iso_z(ingested_at), "event_hash": event_hash}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def _send_message(pool: BotPool, *, channel_id: str, text: str) -> bool:
    for _ in range(3):
        idx, bot = pool.get()
        try:
            await bot.send_message(
                chat_id=channel_id,
                text=text,
                disable_web_page_preview=True,
                disable_notification=True,
            )
            return True
        except RetryAfter as exc:
            pool.cooldown(idx, float(exc.retry_after) + 1)
        except (TimedOut, NetworkError):
            await asyncio.sleep(1)
        except Exception as exc:
            LOG.error("发送失败: %s", exc)
            return False
    return False


def _build_mode(name: str) -> PublishMode:
    _, data_dir = _service_paths()
    if name == "publisher":
        return PublishMode(
            name="publisher",
            channel_id=_env_first("TG_PUBLISHER_CHANNEL_ID", "TG_CHANNEL_ID"),
            bot_tokens=_env_csv("TG_PUBLISHER_BOT_TOKENS", "TG_BOT_TOKENS", "BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
            poll_interval=float(_env_first("TG_PUBLISHER_POLL_INTERVAL", "POLL_INTERVAL") or "1"),
            include_tags=set(),
            exclude_tags=set(_env_csv("TG_PUBLISHER_EXCLUDE_TAGS", "EXCLUDE_TAGS") or ["日历", "交易"]),
            cursor_file=data_dir / "publisher_cursor.json",
            format_message=_format_json_event,
        )
    if name == "signal":
        return PublishMode(
            name="signal",
            channel_id=_env_first("TG_SIGNAL_CHANNEL_ID", "TG_CHANNEL_ID"),
            bot_tokens=_env_csv("TG_SIGNAL_BOT_TOKENS", "TG_BOT_TOKENS", "BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
            poll_interval=float(_env_first("TG_SIGNAL_POLL_INTERVAL", "POLL_INTERVAL") or "1"),
            include_tags=set(_env_csv("TG_SIGNAL_INCLUDE_TAGS", "INCLUDE_TAGS") or ["信号"]),
            exclude_tags=set(),
            cursor_file=data_dir / "signal_cursor.json",
            format_message=_format_signal_event,
        )
    raise ValueError(f"unknown_mode={name}")


def _validate_mode(mode: PublishMode) -> None:
    missing: list[str] = []
    if not mode.bot_tokens:
        missing.append("BOT_TOKEN/TG_*_BOT_TOKENS")
    if not mode.channel_id:
        missing.append("TG_*_CHANNEL_ID")
    if not _env_first("QUERY_SERVICE_BASE_URL"):
        missing.append("QUERY_SERVICE_BASE_URL")
    auth_mode = (_env_first("QUERY_SERVICE_AUTH_MODE") or "required").lower()
    if auth_mode not in {"disabled", "off"} and not _env_first("QUERY_SERVICE_TOKEN"):
        missing.append("QUERY_SERVICE_TOKEN")
    if missing:
        raise RuntimeError(f"缺少配置: {', '.join(missing)}")


async def run(mode_name: str) -> int:
    mode = _build_mode(mode_name)
    _validate_mode(mode)
    LOG.info("启动模式=%s channel=%s include=%s exclude=%s", mode.name, mode.channel_id, sorted(mode.include_tags), sorted(mode.exclude_tags))

    query = QueryEventsClient()
    pool = BotPool(mode.bot_tokens)
    cursor = _load_cursor(mode.cursor_file)
    if cursor is None:
        cursor = query.latest_cursor()
        _save_cursor(mode.cursor_file, ingested_at=cursor[0], event_hash=cursor[1])
    cursor_ts, cursor_hash = cursor
    LOG.info("起始游标=%s %s", _iso_z(cursor_ts), cursor_hash[:12])

    while True:
        events = query.fetch_events(since_ts=cursor_ts, since_hash=cursor_hash)
        for event in events:
            next_ts = _parse_iso(str(event.get("ingested_at") or ""))
            next_hash = str(event.get("event_hash") or "")
            tag = str(event.get("tag") or "")

            matched = True
            if mode.include_tags and tag not in mode.include_tags:
                matched = False
            if mode.exclude_tags and tag in mode.exclude_tags:
                matched = False

            if not matched:
                cursor_ts, cursor_hash = next_ts, next_hash
                _save_cursor(mode.cursor_file, ingested_at=cursor_ts, event_hash=cursor_hash)
                continue

            if await _send_message(pool, channel_id=mode.channel_id, text=mode.format_message(event)):
                cursor_ts, cursor_hash = next_ts, next_hash
                _save_cursor(mode.cursor_file, ingested_at=cursor_ts, event_hash=cursor_hash)
            else:
                LOG.warning("发送失败，保留当前游标等待下轮重试: %s", next_hash[:12])
                break
            await asyncio.sleep(0.05)

        await asyncio.sleep(mode.poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram 导出器：publisher/signal")
    parser.add_argument("mode", choices=["publisher", "signal"])
    args = parser.parse_args()

    base_dir, _ = _service_paths()
    log_file = base_dir / "logs" / f"{args.mode}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file)],
        force=True,
    )

    try:
        return asyncio.run(run(args.mode))
    except KeyboardInterrupt:
        LOG.info("收到中断，退出")
        return 0
    except Exception as exc:
        LOG.error("导出器启动失败: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

