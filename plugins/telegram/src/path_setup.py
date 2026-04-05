from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """
    定位仓库根目录（tradecat/）：
    - 必须同时存在：core/ + plugins/ + assets/ + assets/config/.env.example（或 legacy config/.env.example）
    """
    start = start.resolve()
    for p in [start] + list(start.parents):
        has_core = (p / "core").is_dir()
        has_plugins = (p / "plugins").is_dir()
        has_assets = (p / "assets").is_dir()
        has_env_example = (p / "assets" / "config" / ".env.example").exists() or (p / "config" / ".env.example").exists()
        if has_core and has_plugins and has_assets and has_env_example:
            return p
    return start.parents[4]


def _resolve_env_file(repo_root: Path) -> Path:
    candidates = [
        repo_root / "assets" / "config" / ".env",
        repo_root / "config" / ".env",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


@dataclass(frozen=True)
class ServicePaths:
    src_root: Path
    service_root: Path
    repo_root: Path
    env_file: Path
    data_dir: Path
    assets_dir: Path
    animation_dir: Path
    locale_store: Path


@lru_cache(maxsize=1)
def get_service_paths() -> ServicePaths:
    src_root = Path(__file__).resolve().parent
    service_root = src_root.parent
    repo_root = find_repo_root(src_root)
    data_dir = service_root / "data"
    assets_dir = service_root / "assets"
    return ServicePaths(
        src_root=src_root,
        service_root=service_root,
        repo_root=repo_root,
        env_file=_resolve_env_file(repo_root),
        data_dir=data_dir,
        assets_dir=assets_dir,
        animation_dir=assets_dir / "animations",
        locale_store=data_dir / "user_locale.json",
    )


def load_shared_env() -> Path:
    env_file = get_service_paths().env_file
    if not env_file.exists():
        return env_file
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
    return env_file


def _prepend_sys_path(paths: list[Path]) -> None:
    uniq: list[str] = []
    for p in paths:
        try:
            if not p or not p.is_dir():
                continue
        except Exception:
            continue
        s = str(p)
        if not s or s in uniq:
            continue
        uniq.append(s)
    if not uniq:
        return
    sys.path[:] = uniq + [p for p in sys.path if p not in uniq]


def ensure_runtime_sys_path() -> Path:
    """
    统一注入运行时依赖路径（幂等）：
    - repo root：允许 `import assets.*`
    - ai-service：允许 `import src.pipeline`（ai-service 的 src 包）
    - signal：允许 `import rules/engines/events/...`
    - vis：允许 `import core/templates/...`（vis 的模块根）
    - trading：允许 `import indicators/...`（供 vis_handler 复用指标计算）
    - telegram-service/src：允许 `import bot/cards/signals`

    ⚠️ 重要：路径注入顺序必须避免模块名冲突。
    例如 signal-service 的 `config.py` 与 telegram-service 的 `config/` 包同名，
    若 telegram 路径优先，会导致 signal-service 误导入 telegram 的 config 并启动失败。
    """
    paths = get_service_paths()
    telegram_src = paths.src_root
    repo_root = paths.repo_root

    # 先放 repo_root（assets.*），再放其它 service 依赖，最后放 telegram 自身模块根。
    candidates: list[Path] = [repo_root]

    # ai-service（包名为 src）
    for p in (
        repo_root / "plugins" / "ai-service",
        repo_root / "plugins" / "ai",
    ):
        if p.is_dir():
            candidates.append(p)
            break

    # signal（模块根为 src 目录）
    for p in (
        repo_root / "plugins" / "signal" / "src",
        repo_root / "plugins" / "signal-service" / "src",
    ):
        if p.is_dir():
            candidates.append(p)
            break

    # vis（模块根为 src 目录）
    for p in (
        repo_root / "plugins" / "vis" / "src",
        repo_root / "plugins" / "vis-service" / "src",
    ):
        if p.is_dir():
            candidates.append(p)
            break

    # trading（模块根为 src 目录）
    for p in (
        repo_root / "plugins" / "trading" / "src",
        repo_root / "plugins" / "trading-service" / "src",
    ):
        if p.is_dir():
            candidates.append(p)
            break

    candidates.append(telegram_src)

    _prepend_sys_path(candidates)
    return repo_root
