from __future__ import annotations

from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """
    定位仓库根目录（tradecat/）：
    - 必须同时存在：core/ + plugins/ + assets/ + assets/config/.env.example（或 legacy config/.env.example）
    """
    for p in [start] + list(start.parents):
        has_core = (p / "core").is_dir()
        has_plugins = (p / "plugins").is_dir()
        has_assets = (p / "assets").is_dir()
        has_env_example = (p / "assets" / "config" / ".env.example").exists() or (p / "config" / ".env.example").exists()
        if has_core and has_plugins and has_assets and has_env_example:
            return p
    raise RuntimeError(f"无法定位 repo root（从 {start} 向上未找到 core/plugins + assets/config/.env.example）")


def find_telegram_service_src(repo_root: Path) -> Path:
    candidates = [
        repo_root / "plugins" / "telegram" / "src",
        repo_root / "plugins" / "telegram-service" / "src",
    ]
    for p in candidates:
        if p.is_dir():
            return p
    raise RuntimeError(f"无法定位 telegram-service/src（尝试过：{', '.join(str(c) for c in candidates)}）")
