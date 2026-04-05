"""配置管理（只读加载共享 .env；服务自身 .env 不提交）"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


def _find_project_root(start: Path) -> Path:
    current = start.resolve()
    for path in [current] + list(current.parents):
        if not (path / "assets").is_dir():
            continue
        has_env_example = (path / "assets" / "config" / ".env.example").exists() or (path / "config" / ".env.example").exists()
        has_layout = (path / "core").is_dir() or (path / "plugins").is_dir()
        if has_env_example and has_layout:
            return path
    return current.parents[4]


def _resolve_env_file(project_root: Path) -> Path:
    candidates = [
        project_root / "assets" / "config" / ".env",
        project_root / "config" / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


SERVICE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = _find_project_root(Path(__file__))
ENV_FILE = _resolve_env_file(PROJECT_ROOT)


@lru_cache(maxsize=1)
def load_shared_env() -> Path:
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)
    return ENV_FILE


load_shared_env()


def _resolve_repo_path(env_key: str, default: Path) -> Path:
    raw = (os.getenv(env_key) or "").strip()
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else (PROJECT_ROOT / p)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


class Settings:
    """服务配置（实例化时读取环境变量，配合 get_settings() 做缓存）。"""

    def __init__(self) -> None:
        # 服务监听
        self.HOST: str = os.getenv("LLM_GATEWAY_HOST", "0.0.0.0")
        self.PORT: int = int(os.getenv("LLM_GATEWAY_PORT", "9010"))
        self.DEBUG: bool = (os.getenv("LLM_GATEWAY_DEBUG", "false").strip().lower() == "true")

        # 鉴权（nofx -> gateway）
        self.TOKEN: str = (os.getenv("LLM_GATEWAY_TOKEN") or "").strip()

        # 组装策略：inject（默认）| passthrough（仅透传，不拼 context vars）
        self.MODE: str = (os.getenv("LLM_GATEWAY_MODE") or "inject").strip().lower()

        # 请求体 / prompt 上限（防 DoS）
        self.MAX_BODY_BYTES: int = int(os.getenv("LLM_GATEWAY_MAX_BODY_BYTES", "1048576"))  # 1 MiB
        self.MAX_MESSAGES: int = int(os.getenv("LLM_GATEWAY_MAX_MESSAGES", "32"))
        self.MAX_MESSAGE_CHARS: int = int(os.getenv("LLM_GATEWAY_MAX_MESSAGE_CHARS", "200000"))

        # 资产落盘
        self.RUN_ASSETS_ENABLED: bool = (os.getenv("LLM_GATEWAY_RUN_ASSETS_ENABLED", "true").strip().lower() == "true")
        self.RUN_ASSETS_DIR: Path = _resolve_repo_path(
            "LLM_GATEWAY_RUN_ASSETS_DIR",
            PROJECT_ROOT / "plugins" / "llm-gateway-service" / "data" / "run_asset",
        )

        # 上游模型（gateway -> upstream）
        self.UPSTREAM_BASE_URL: str = (os.getenv("LLM_GATEWAY_UPSTREAM_BASE_URL") or "").strip()
        self.UPSTREAM_API_KEY: str = (os.getenv("LLM_GATEWAY_UPSTREAM_API_KEY") or "").strip()
        self.UPSTREAM_MODEL: str = (os.getenv("LLM_GATEWAY_UPSTREAM_MODEL") or "").strip()
        self.UPSTREAM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_GATEWAY_UPSTREAM_TIMEOUT_SECONDS", "30"))
        self.UPSTREAM_ALLOWLIST: str = (os.getenv("LLM_GATEWAY_UPSTREAM_ALLOWLIST") or "").strip()
        self.UPSTREAM_REQUIRE_HTTPS: bool = (
            (os.getenv("LLM_GATEWAY_UPSTREAM_REQUIRE_HTTPS", "true").strip().lower() == "true")
        )
        self.UPSTREAM_BLOCK_PRIVATE: bool = (
            (os.getenv("LLM_GATEWAY_UPSTREAM_BLOCK_PRIVATE", "true").strip().lower() == "true")
        )

        # Tradecat Query Service（只读）
        self.QUERY_SERVICE_BASE_URL: str = (os.getenv("QUERY_SERVICE_BASE_URL") or "").strip()
        self.QUERY_SERVICE_TOKEN: str = (os.getenv("QUERY_SERVICE_TOKEN") or "").strip()
        self.QUERY_SERVICE_TIMEOUT_SECONDS: float = float(os.getenv("QUERY_SERVICE_TIMEOUT_SECONDS", "8"))

        # Prompt 长度控制（字符数；MVP 先用 chars 粗控，后续可换 token 估算）
        self.CONTEXT_MD_MAX_CHARS: int = int(os.getenv("LLM_GATEWAY_CONTEXT_MD_MAX_CHARS", "8000"))

        # 原材料抓取配置（按服务落盘；不会默认塞进 prompt）
        self.RAW_SYMBOLS: list[str] = _parse_csv(os.getenv("LLM_GATEWAY_RAW_SYMBOLS"))
        self.RAW_CARDS: list[str] = _parse_csv(os.getenv("LLM_GATEWAY_RAW_CARDS"))
        self.RAW_CARD_INTERVAL: str = (os.getenv("LLM_GATEWAY_RAW_CARD_INTERVAL") or "15m").strip()
        self.RAW_CARD_LIMIT: int = int(os.getenv("LLM_GATEWAY_RAW_CARD_LIMIT", "200"))
        self.RAW_SNAPSHOT_PANELS: list[str] = _parse_csv(os.getenv("LLM_GATEWAY_RAW_SNAPSHOT_PANELS"))
        self.RAW_SNAPSHOT_INTERVALS: list[str] = _parse_csv(os.getenv("LLM_GATEWAY_RAW_SNAPSHOT_INTERVALS"))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
