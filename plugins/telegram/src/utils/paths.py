"""路径工具"""

from pathlib import Path

try:
    from ..path_setup import get_service_paths
except ImportError:  # pragma: no cover - 兼容直接脚本执行
    from path_setup import get_service_paths  # type: ignore


SERVICE_PATHS = get_service_paths()
SERVICE_ROOT = SERVICE_PATHS.service_root
PROJECT_ROOT = SERVICE_PATHS.repo_root
DATABASE_DIR = PROJECT_ROOT / "assets" / "database" / "services" / "telegram-service"


def 获取数据库目录() -> Path:
    """返回 telegram-service 数据库目录"""
    return DATABASE_DIR


__all__ = ["获取数据库目录", "SERVICE_ROOT", "PROJECT_ROOT", "DATABASE_DIR"]
