"""LLM Gateway Service 入口"""

import os

import uvicorn

from src.config import get_settings


def main():
    # 安全默认：避免 run_asset 落盘出现“组/其他可读”的多世界
    os.umask(0o077)

    settings = get_settings()
    print(f"启动 LLM Gateway Service: http://{settings.HOST}:{settings.PORT}")
    print(f"健康检查: http://{settings.HOST}:{settings.PORT}/healthz")

    uvicorn.run(
        "src.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
    )


if __name__ == "__main__":
    main()
