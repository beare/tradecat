"""环境与路径工具。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True, slots=True)
class ProjectLayout:
    project_root: Path
    stack_root: Path
    env_file: Path | None


def find_project_root(start: Path) -> Path:
    """向上查找仓库根目录。"""
    current = start.resolve()
    for _ in range(12):
        has_assets = (current / "assets").is_dir()
        has_env_example = (current / "assets" / "config" / ".env.example").exists() or (current / "config" / ".env.example").exists()
        has_layout = (current / "core").is_dir() or (current / "plugins").is_dir()
        if has_assets and has_env_example and has_layout:
            return current
        if current.parent == current:
            break
        current = current.parent
    raise RuntimeError(f"无法从 {start} 定位仓库根目录")


def find_env_file(project_root: Path) -> Path | None:
    for candidate in [
        project_root / "assets" / "config" / ".env",
        project_root / "config" / ".env",
    ]:
        if candidate.exists():
            return candidate
    return None


def ensure_env_permissions(env_file: Path | None) -> None:
    if env_file is None:
        return
    path_str = str(env_file)
    if not (path_str.endswith("assets/config/.env") or path_str.endswith("config/.env")):
        return
    perm = oct(env_file.stat().st_mode & 0o777)[2:]
    if perm not in {"600", "400"} and os.getenv("CODESPACES") != "true":
        raise RuntimeError(f"{env_file} 权限为 {perm}，必须设为 600 或 400")


def load_env_defaults(env_file: Path | None) -> None:
    """只加载默认值，不覆盖进程外部显式环境变量。"""
    if env_file is None or not env_file.exists():
        return
    ensure_env_permissions(env_file)
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            continue
        if "$(" in line or "`" in line:
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
        if value.startswith("'") and value.endswith("'") and len(value) >= 2:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def bool_env(name: str, default: bool = False) -> bool:
    return parse_bool(os.getenv(name), default=default)


def first_env(names: Sequence[str], default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return default


def first_bool_env(names: Sequence[str], default: bool = False) -> bool:
    for name in names:
        if name in os.environ:
            return parse_bool(os.getenv(name), default=default)
    return default


def csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_layout(stack_root: Path) -> ProjectLayout:
    project_root = find_project_root(stack_root)
    env_file = find_env_file(project_root)
    return ProjectLayout(project_root=project_root, stack_root=stack_root, env_file=env_file)
